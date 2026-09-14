# -*- coding: utf-8 -*-
"""
Tín hiệu tái xếp hạng bổ sung dựa trên NLI (Natural Language Inference).

Bối cảnh: tầng 3 hiện tại phát hiện đối lập ngữ nghĩa bằng `POLARITY_AXES` -
5 trục liệt kê TAY (típ tiểu đường, nguyên nhân THA, biến chứng, cấp/mạn, dị
ứng). Cơ chế này chỉ bắt được đúng những cặp đối lập mà người viết luật đã
nghĩ tới; gặp cặp đối lập mới (ví dụ một bệnh khác cũng phân biệt theo "có
di căn / không di căn" mà chưa ai liệt kê) thì hệ thống lại mù y hệt lý do nó
mù với "tuýp 2" (mục A.2 README).

Module này thêm một tín hiệu THỨ HAI, không thay thế POLARITY_AXES: dùng một
mô hình NLI đa ngôn ngữ đã huấn luyện sẵn (zero-shot, KHÔNG cần huấn luyện
riêng cho ICD-10 - không đụng tới việc thu thập dữ liệu lâm sàng mới) để hỏi
trực tiếp "câu chẩn đoán và tên mã này có MÂU THUẪN logic không". Áp dụng
CASCADE: chỉ chạy cho một số nhỏ ứng viên đứng đầu sau tái xếp hạng luật (mặc
định 5), vì NLI phải chạy transformer cho từng CẶP câu - không cache trước
được như embedding truy hồi ở tầng 2.

An toàn khi tích hợp:
    - Mặc định TẮT (`use_nli=False` trên NLPEngine) - không đổi hành vi hiện
      tại trừ khi bật rõ ràng.
    - Chỉ cộng/trừ một khoản NHỎ vào rerank_score hiện có, không thay thế
      điểm ngữ nghĩa/luật - tránh một mô hình chưa kiểm chứng kỹ trên thuật
      ngữ y khoa tiếng Việt lật ngược quyết định của luật đã kiểm chứng.
    - KHÔNG được đè lên ràng buộc cứng (tuổi/giới/dagger-asterisk) - những
      ràng buộc đó áp dụng SAU, độc lập với NLI, đúng nguyên tắc "luật có căn
      cứ quy phạm không nên để mô hình học xấp xỉ".
"""

import os
from typing import Dict, List, Optional, Tuple

DEFAULT_MODEL = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
# Ghim đúng bản đã đo số liệu trong nlp/README.md. Không ghim thì repo trên
# HuggingFace Hub có thể cập nhật trọng số sau này mà code vẫn tự tải bản mới
# nhất - nhóm chạy lại 6 tháng sau có thể ra số khác mà không rõ vì sao.
DEFAULT_REVISION = "b5113eb38ab63efdd7f280f8c144ea8b13f978ce"

# Cách diễn đạt câu hỏi cho NLI. "premise" là câu bác sĩ (đã chuẩn hóa),
# "hypothesis" dựng từ tên mã theo mẫu này. Đây là một trong các tham số cần
# dò trong quá trình thử nghiệm (nlp/bench.py --nli-template).
HYPOTHESIS_TEMPLATES = {
    "bare": "{name}",
    "patient": "Bệnh nhân được chẩn đoán {name}",
    "diagnosis": "Chẩn đoán là {name}",
}


class NLIReranker:
    """Bọc một mô hình NLI HuggingFace, trả điểm entail/neutral/contradiction."""

    def __init__(self, model_name: str = DEFAULT_MODEL, device: Optional[str] = None,
                 revision: Optional[str] = None):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        # Chỉ ghim revision khi dùng đúng model mặc định - truyền model_name
        # khác (thử nghiệm model mới) thì để trống, không ép theo commit của
        # model cũ.
        if revision is None and model_name == DEFAULT_MODEL:
            revision = DEFAULT_REVISION

        self.tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name, revision=revision)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        self.model.eval()

        # Vị trí nhãn KHÔNG cố định giữa các checkpoint - tra theo tên nhãn
        # trong config thay vì giả định thứ tự [entailment, neutral, contradiction].
        id2label = {int(k): str(v).lower() for k, v in self.model.config.id2label.items()}
        self._idx = {name: i for i, name in id2label.items()}
        for required in ("entailment", "neutral", "contradiction"):
            if required not in self._idx:
                raise ValueError(
                    f"Model {model_name} không có nhãn '{required}' - "
                    f"nhãn thực tế: {id2label}")

    def score_batch(self, premise: str, hypotheses: List[str]) -> List[Dict[str, float]]:
        """Trả xác suất (entailment, neutral, contradiction) cho từng hypothesis."""
        import torch

        if not hypotheses:
            return []
        premises = [premise] * len(hypotheses)
        with torch.no_grad():
            enc = self.tokenizer(
                premises, hypotheses, return_tensors="pt",
                padding=True, truncation=True, max_length=64,
            ).to(self.device)
            logits = self.model(**enc).logits
            probs = torch.softmax(logits, dim=-1).cpu().tolist()

        out = []
        for p in probs:
            out.append({
                "entailment": p[self._idx["entailment"]],
                "neutral": p[self._idx["neutral"]],
                "contradiction": p[self._idx["contradiction"]],
            })
        return out


def apply_nli_rerank(
    nli: NLIReranker,
    premise: str,
    candidates: List[Tuple[str, str]],  # [(code, name_vi), ...] đã sắp theo rerank_score
    weight: float = 0.20,
    template: str = "bare",
    top_k: int = 5,
    contradiction_only: bool = True,
    contradiction_floor: float = 0.5,
) -> Dict[str, Tuple[float, List[str]]]:
    """
    Chấm điều chỉnh NLI cho top_k ứng viên đầu (đã sắp theo luật ở tầng 3).

    Trả {code: (delta_diem, ghi_chu)}. Chỉ trả về cho các mã THỰC SỰ được đưa
    qua NLI (top_k đầu) - mã ngoài phạm vi này không bị đụng tới, giữ đúng
    tinh thần cascade: lọc thô bằng luật (rẻ), tinh chỉnh bằng NLI (đắt hơn)
    trên một tập đã thu hẹp.

    `contradiction_only=True` (mặc định, đã kiểm chứng bằng thử nghiệm) - CHỈ
    trừ điểm khi contradiction vượt `contradiction_floor`, KHÔNG cộng điểm
    theo entailment. Lý do: đo trên eval_set + holdout cho thấy tín hiệu
    contradiction đáng tin (đúng phát hiện "Hen, không phân loại" mâu thuẫn
    với câu có nêu thể; "Suy thận mạn, không đặc hiệu" mâu thuẫn tương tự),
    nhưng tín hiệu entailment thì NHIỄU - từng thưởng điểm nhầm cho mã cha
    chung chung (H10 thay vì H10.1) và một mã hoàn toàn sai chuyên khoa
    (M86.6 "viêm xương tủy" cho câu "viêm dạ dày", có lẽ vì trùng khuôn mẫu
    "viêm ... mãn tính" về mặt câu chữ). Dùng entailment để CỘNG điểm là rủi ro
    lớn hơn lợi ích ở phiên bản mô hình NLI đa ngôn ngữ này.
    """
    scoped = candidates[:top_k]
    if not scoped:
        return {}

    tmpl = HYPOTHESIS_TEMPLATES.get(template, HYPOTHESIS_TEMPLATES["bare"])
    hypotheses = [tmpl.format(name=name) for _, name in scoped]
    scores = nli.score_batch(premise, hypotheses)

    deltas: Dict[str, Tuple[float, List[str]]] = {}
    for (code, name), s in zip(scoped, scores):
        notes = []
        if contradiction_only:
            delta = -weight * s["contradiction"] if s["contradiction"] > contradiction_floor else 0.0
        else:
            delta = weight * (s["entailment"] - s["contradiction"])
        if s["contradiction"] > contradiction_floor:
            notes.append(
                f"NLI: mâu thuẫn ngữ nghĩa với '{name}' "
                f"(contradiction={s['contradiction']:.2f})")
        elif not contradiction_only and s["entailment"] > 0.5:
            notes.append(
                f"NLI: khẳng định phù hợp với '{name}' "
                f"(entailment={s['entailment']:.2f})")
        deltas[code] = (delta, notes)
    return deltas
