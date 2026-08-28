# -*- coding: utf-8 -*-
"""
Bộ xử lý NLP của SMIG Gateway.

Kiến trúc truy hồi lai (hybrid retrieval), 4 tầng:

    1. Chuẩn hóa   - bỏ tiền tố nhiễu, giải nghĩa viết tắt, nối cầu nối từ vựng.
    2. Truy hồi    - cosine similarity trên embedding SBERT đã tiền tính.
    3. Tái xếp hạng- kết hợp điểm ngữ nghĩa + trùng lặp từ vựng + alias lâm sàng,
                     trừ điểm khi vi phạm trục đối lập ngữ nghĩa hoặc cổng chương.
    4. Hiệu chuẩn  - quy đổi điểm cuối sang độ tin cậy và mức cảnh báo.

Tầng 3 là lý do hệ thống phân biệt được E10/E11 và I10/I15 - những cặp mà
cosine similarity thuần túy xếp gần như ngang nhau.
"""

import hashlib
import json
import os
import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer, util

from nlp.clinical_rules import (
    ABBREVIATIONS,
    BRIDGE_TERMS,
    CHAPTER_GATES,
    DATA_FIXES,
    DIRECT_ALIASES,
    NEOPLASM_TERMS,
    NOISE_PREFIXES,
    NOISE_SUFFIXES,
    OTHER_MARKERS,
    OTHER_PENALTY,
    POLARITY_AXES,
    QUERY_ABBREVIATIONS,
    STOPWORDS,
    UNSPECIFIED_BONUS,
    UNSPECIFIED_MARKERS,
    UNSPECIFIED_PENALTY,
    confidence_band,
    rang_buoc_lam_sang,
    thong_nhat_dau_thanh,
    tuoi_theo_ngay,
)

# --- Trọng số tái xếp hạng -------------------------------------------------
# Chênh lệch cosine giữa các mã ICD-10 lân cận thường chỉ 0.005-0.03, nên các
# hệ số dưới đây được đặt đủ lớn để một vi phạm ngữ nghĩa lật được thứ hạng.
W_LEXICAL = 0.12          # thưởng cho trùng lặp từ vựng (F1 trên token nội dung)
W_EXACT = 0.10            # thưởng khi câu truy vấn chứa trọn thuật ngữ tham chiếu
ALIAS_BONUS_PRIMARY = 0.15
ALIAS_BONUS_SECONDARY = 0.10
CANDIDATE_POOL = 400      # số entry đưa vào tái xếp hạng

# --- Hiệu chuẩn độ tin cậy -------------------------------------------------
# Độ tin cậy KHÔNG lấy trực tiếp từ điểm tái xếp hạng: điểm đó có cộng thưởng
# nên dễ vượt trần và mọi kết quả sẽ hiện 100%, làm mất tác dụng của ngưỡng
# duyệt. Thay vào đó kết hợp hai thành phần độc lập:
#   - tương đối: softmax trên điểm tái xếp hạng, đo mức áp đảo so với ứng viên khác
#   - tuyệt đối: độ tương đồng ngữ nghĩa thô, đo mức khớp thật với danh mục
# Một mã thắng áp đảo nhưng ngữ nghĩa xa vẫn bị hạ điểm, và ngược lại.
W_RELATIVE = 0.65
W_ABSOLUTE = 0.35
SOFTMAX_TEMPERATURE = 0.06
SOFTMAX_SCOPE = 20        # số mã đưa vào chuẩn hóa softmax
SIM_FLOOR = 0.40
SIM_CEIL = 0.90

# Sàn tin cậy cho ca khớp trọn nghĩa. Áp dụng khi mọi từ trong câu đều có mặt ở
# tên mã và độ tương đồng thô rất cao - lúc đó việc danh mục còn mã lân cận
# không nói lên điều gì về việc mã này đúng hay sai.
COVERAGE_FULL = 1.0       # câu phải được phủ trọn, thiếu một từ cũng không tính
COVERAGE_FLOOR_SIM = 0.90 # ngưỡng tương đồng thô để được hưởng sàn
COVERAGE_FLOOR_CONF = 0.88

# --- Chẩn đoán kép (dao găm/sao) -------------------------------------------
# Tên mã trong danh mục ghi kèm mã đối tác: "(G55.1*)" hoặc dải "(M50-M51†)".
_CODE_REF_RE = re.compile(
    r"\(\s*([A-Z]\d{2}(?:\.\d+)?)\s*(?:-\s*([A-Z]\d{2}(?:\.\d+)?)\s*)?([†*])\s*\)"
)
# Ranh giới giữa các vế lâm sàng trong một dòng chẩn đoán. KHÔNG tách theo "và":
# "rễ và đám rối thần kinh" là một cụm, tách ra sẽ vỡ nghĩa.
_FRAGMENT_SPLIT_RE = re.compile(
    r"[,;/+]|\bkèm theo\b|\bkèm\b|\bcó biến chứng\b|\bbiến chứng\b|\bgây\b|\bdẫn đến\b",
    re.IGNORECASE,
)
MAX_FRAGMENTS = 6         # số vế tối đa đưa vào truy vấn riêng, chặn chi phí encode
FRAGMENT_TOP_K = 5        # số ứng viên xét cho mỗi vế khi dò cặp †/*
# Cụm ngắn hơn ngần này không đủ nghĩa để coi là một chẩn đoán riêng.
MIN_TERM_FRAGMENT_LEN = 5
# Ngưỡng để coi một vế là chẩn đoán độc lập. Đặt cao vì hậu quả của việc tách
# nhầm (sinh thêm một bệnh không có thật trong hồ sơ) nặng hơn việc bỏ sót:
# vế mô tả bổ sung cho bệnh chính thường chỉ đạt 30-50% khi tra riêng.
MULTI_DIAGNOSIS_MIN_CONF = 60.0

_NOISE_RE = [re.compile(p) for p in NOISE_PREFIXES]
_NOISE_SUFFIX_RE = [re.compile(p) for p in NOISE_SUFFIXES]
_ABBR_RE = [(re.compile(p), r) for p, r in ABBREVIATIONS.items()]
_QUERY_ABBR_RE = [(re.compile(p), r) for p, r in QUERY_ABBREVIATIONS.items()]
_MAX_NER_NGRAM = 12
# Danh mục sinh cả biến thể mã rút gọn làm synonym ("a988"). Chúng khớp NER như
# một thuật ngữ nhưng không phải tên bệnh, không được tính là một chẩn đoán.
_CODE_LIKE_RE = re.compile(r"^[a-z]\d{2,4}$")
_NEOPLASM_RE = re.compile(r"\b(?:%s)\b" % "|".join(NEOPLASM_TERMS))


def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt, dùng để so khớp các biến thể gõ không dấu."""
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def sanitize_icd10_code(code: str) -> str:
    """
    Loại bỏ ký hiệu dao găm/sao (†, *, ‡) khỏi mã ICD-10.

    Danh mục của Bộ Y tế dùng † để đánh dấu mã bệnh nguyên phát trong hệ thống
    dagger/asterisk. Ký tự này KHÔNG thuộc mã ICD-10 và sẽ bị máy chủ FHIR từ
    chối, nên phải gỡ trước khi đưa vào tài nguyên liên thông.
    """
    return re.sub(r"[†*‡]", "", code or "").strip()


def code_no_dot(code: str) -> str:
    """
    Dạng liền không dấu chấm của mã ICD-10 (A00.0 -> A000).

    Danh mục Bộ Y tế có sẵn cột "MÃ BỆNH KHÔNG DẤU" và nó đúng bằng mã đã gỡ
    dấu chấm, nên suy ra tại chỗ thay vì phụ thuộc vào danh mục có trường đó
    hay không. Chỉ dùng cho HIS/báo cáo: FHIR yêu cầu dạng có dấu chấm.
    """
    return sanitize_icd10_code(code).replace(".", "")


class NLPEngine:
    """Tự động dùng mô hình cục bộ nếu có, nếu không thì tải mô hình online."""

    def __init__(self, db_path: str = None, model_name: str = None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), "data", "icd10_db.json")

        print(f"Loading ICD-10 database from: {db_path}")
        with open(db_path, "r", encoding="utf-8") as f:
            self.db = json.load(f)

        # Tra cứu mã -> bản ghi trong O(1), thay cho quét tuyến tính mỗi lần truy vấn.
        self.db_index: Dict[str, dict] = {e["code"]: e for e in self.db}

        if model_name is None:
            local_model_path = os.path.join(os.path.dirname(__file__), "my_medical_nlp_model")
            if os.path.isdir(local_model_path):
                model_name = local_model_path
            else:
                print(f"Thư mục mô hình cục bộ '{local_model_path}' không tồn tại.")
                print("-> Tự động chuyển sang tải mô hình online: 'Keepitreal/vietnamese-sbert'")
                model_name = "Keepitreal/vietnamese-sbert"
        elif model_name.startswith("./") or model_name.startswith("../"):
            model_name = os.path.abspath(os.path.join(os.path.dirname(__file__), model_name))

        self.model_name = model_name
        self._fingerprint: Optional[str] = None
        print(f"Loading NLP Model: {model_name} (this may take a minute on first run)...")
        self.model = SentenceTransformer(model_name)

        # Thứ tự bắt buộc: embedding của danh mục phải được dựng từ dữ liệu GỐC,
        # nếu không khóa cache sẽ đổi và hệ thống phải tính lại ~47k vector.
        # Bản vá dữ liệu chỉ áp cho tầng hiển thị/luật, và được đưa vào tập alias
        # (nhỏ, tính trong vài giây) để vẫn có mặt trong không gian embedding.
        self._prepare_embeddings()
        self._apply_data_fixes()
        self._prepare_alias_embeddings()
        self._build_lookup_tables()
        print(f"NLP Engine initialized successfully! "
              f"({len(self.reference_entries)} vector tham chiếu, {len(self.db)} mã ICD-10)")

    # ------------------------------------------------------------------
    # Khởi tạo
    # ------------------------------------------------------------------
    def _apply_data_fixes(self):
        """Sửa các dòng dữ liệu sai lệch của file danh mục nguồn."""
        fixed = 0
        for entry in self.db:
            patch = DATA_FIXES.get(entry["code"])
            if patch:
                entry.update(patch)
                fixed += 1
        if fixed:
            print(f"Đã áp dụng {fixed} bản vá dữ liệu danh mục nguồn.")

    def _model_fingerprint(self) -> str:
        """
        Vân tay của TRỌNG SỐ mô hình cục bộ.

        Khóa cache bản trước chỉ gồm TÊN thư mục mô hình, nên khi fine-tune lại
        vào cùng thư mục, hệ thống vẫn nạp vector của checkpoint cũ: một truy vấn
        trùng khít mục từ ("đậu khỉ" -> "Đậu khỉ") chỉ đạt cosine 0.77 thay vì
        1.0, kéo độ tin cậy xuống ~86% và làm lệch thứ hạng.

        Chỉ băm kích thước + 1 MB đầu và 1 MB cuối của file trọng số thay vì toàn
        bộ ~540 MB, để không thêm vài giây vào mỗi lần khởi động; huấn luyện lại
        luôn làm đổi các byte này. Dùng NỘI DUNG file chứ không dùng mtime để
        cache vẫn hiệu lực khi chép dự án sang máy khác.
        """
        if self._fingerprint is not None:
            return self._fingerprint

        if not os.path.isdir(self.model_name):
            # Mô hình trên hub: tên đã bao hàm định danh phiên bản.
            self._fingerprint = ""
            return self._fingerprint

        chunk = 1 << 20
        digest = hashlib.sha1()
        for relative in ("model.safetensors", "pytorch_model.bin", "model.onnx",
                         "config.json", "sentence_bert_config.json",
                         os.path.join("1_Pooling", "config.json")):
            path = os.path.join(self.model_name, relative)
            if not os.path.exists(path):
                continue
            size = os.path.getsize(path)
            digest.update(f"{relative}:{size}".encode("utf-8"))
            try:
                with open(path, "rb") as f:
                    digest.update(f.read(chunk))
                    if size > 2 * chunk:
                        f.seek(-chunk, os.SEEK_END)
                        digest.update(f.read(chunk))
            except OSError:
                continue

        self._fingerprint = digest.hexdigest()[:12]
        return self._fingerprint

    def _cache_matches_model(self, emb_path: str, entries: List[dict]) -> bool:
        """
        Xác nhận vector trong cache đúng là do mô hình đang nạp sinh ra.

        Mã hóa lại vài mục từ mẫu rồi so cosine với vector tương ứng trong cache:
        cùng mô hình thì phải xấp xỉ 1.0. Đây là lưới an toàn cuối cùng cho các
        trường hợp vân tay không bắt được (cache chép tay, đổi tên file...).
        """
        try:
            cached = np.load(emb_path, mmap_mode="r")
        except Exception:
            return False
        if cached.shape[0] != len(entries) or not len(entries):
            return False

        probes = sorted({0, len(entries) // 2, len(entries) - 1})
        fresh = self.model.encode(
            [self.normalize_text(entries[i]["text"]) for i in probes],
            convert_to_tensor=False,
        )
        for row, idx in enumerate(probes):
            a = np.asarray(fresh[row], dtype=np.float64)
            b = np.asarray(cached[idx], dtype=np.float64)
            norm = np.linalg.norm(a) * np.linalg.norm(b)
            if norm == 0 or float(np.dot(a, b) / norm) < 0.999:
                return False
        return True

    def _cache_key(self) -> Tuple[str, str]:
        """
        Sinh khóa cache từ (định danh mô hình, nội dung danh mục).

        Dùng tên thư mục + vân tay trọng số thay cho đường dẫn tuyệt đối để cache
        còn dùng được khi chép dự án sang máy khác nhưng tự vô hiệu khi mô hình
        được huấn luyện lại, và dùng hash NỘI DUNG danh mục thay cho số lượng bản
        ghi để không bao giờ nạp nhầm cache cũ khi danh mục đổi nội dung mà giữ
        nguyên số dòng.
        """
        if os.path.isdir(self.model_name):
            model_key = os.path.basename(os.path.normpath(self.model_name))
        else:
            model_key = self.model_name.replace("/", "_").replace("\\", "_").replace(":", "_")

        fingerprint = self._model_fingerprint()
        if fingerprint:
            model_key = f"{model_key}_{fingerprint}"

        digest = hashlib.sha1()
        for entry in self.db:
            digest.update(entry["code"].encode("utf-8"))
            digest.update(entry["name_vi"].encode("utf-8"))
            digest.update((entry.get("name_en") or "").encode("utf-8"))
            for syn in entry.get("synonyms", []):
                digest.update(syn.encode("utf-8"))
        return model_key, digest.hexdigest()[:12]

    def _build_reference_entries(self) -> Tuple[List[dict], List[str]]:
        entries, texts = [], []
        for entry in self.db:
            code = entry["code"]
            entries.append({"code": code, "text": entry["name_vi"], "type": "official_vi"})
            texts.append(self.normalize_text(entry["name_vi"]))

            if entry.get("name_en"):
                entries.append({"code": code, "text": entry["name_en"], "type": "official_en"})
                texts.append(self.normalize_text(entry["name_en"]))

            for synonym in entry.get("synonyms", []):
                entries.append({"code": code, "text": synonym, "type": "synonym"})
                texts.append(self.normalize_text(synonym))
        return entries, texts

    def _migrate_legacy_cache(self, entries: List[dict], emb_path: str, ent_path: str) -> bool:
        """
        Nhận diện cache của phiên bản cũ (đặt tên theo đường dẫn tuyệt đối và số
        lượng bản ghi) rồi đổi tên sang khóa mới, thay vì tính lại ~47k embedding.
        """
        data_dir = os.path.dirname(emb_path)
        if not os.path.isdir(data_dir):
            return False

        for fname in sorted(os.listdir(data_dir)):
            if not (fname.startswith("entries_") and fname.endswith(".json")):
                continue
            legacy_ent = os.path.join(data_dir, fname)
            legacy_emb = os.path.join(data_dir, "embeddings_" + fname[len("entries_"):-len(".json")] + ".npy")
            if legacy_ent == ent_path or not os.path.exists(legacy_emb):
                continue
            try:
                with open(legacy_ent, "r", encoding="utf-8") as f:
                    legacy_entries = json.load(f)
            except Exception:
                continue
            if legacy_entries != entries:
                continue

            # Trùng danh sách mục từ KHÔNG có nghĩa là cùng mô hình: mọi checkpoint
            # đều sinh ra đúng ngần ấy vector. Thiếu bước này, cache của mô hình cũ
            # bị đổi tên sang khóa mới và âm thầm làm sai toàn bộ điểm cosine.
            if not self._cache_matches_model(legacy_emb, entries):
                print(f"Bỏ qua cache cũ {fname}: vector không do mô hình hiện tại sinh ra.")
                continue

            print(f"Phát hiện cache cũ tương thích ({fname}) -> chuyển sang khóa mới, bỏ qua bước tính lại.")
            os.replace(legacy_emb, emb_path)
            os.replace(legacy_ent, ent_path)
            return True
        return False

    def _prepare_embeddings(self):
        """Tiền tính embedding cho toàn bộ danh mục, có cache trên đĩa."""
        import torch

        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        model_key, db_hash = self._cache_key()
        emb_path = os.path.join(data_dir, f"embeddings_{model_key}_{db_hash}.npy")
        ent_path = os.path.join(data_dir, f"entries_{model_key}_{db_hash}.json")

        entries, texts = self._build_reference_entries()

        if not (os.path.exists(emb_path) and os.path.exists(ent_path)):
            self._migrate_legacy_cache(entries, emb_path, ent_path)

        if os.path.exists(emb_path) and os.path.exists(ent_path):
            print("Loading pre-computed embeddings cache (Instant launch)...")
            try:
                with open(ent_path, "r", encoding="utf-8") as f:
                    self.reference_entries = json.load(f)
                # Lưới an toàn cuối: cache sai mô hình làm mọi điểm cosine lệch mà
                # không có lỗi nào được ném ra, nên phải phát hiện tại đây.
                if not self._cache_matches_model(emb_path, self.reference_entries):
                    raise ValueError("cache không khớp mô hình đang nạp")
                self.reference_embeddings = torch.from_numpy(np.load(emb_path)).to(self.model.device)
                print(f"Loaded {len(self.reference_entries)} reference embeddings from cache.")
                return
            except Exception as e:
                print(f"Error loading cache, re-computing embeddings: {e}")

        self.reference_entries = entries
        print("=" * 70)
        print(f"CẢNH BÁO: không tìm thấy cache tương thích. Hệ thống sẽ mã hóa")
        print(f"{len(texts)} thuật ngữ tham chiếu - việc này mất 15-40 phút trên CPU.")
        print("Kết quả được lưu lại nên các lần khởi động sau sẽ tức thì.")
        print("=" * 70)
        self.reference_embeddings = self.model.encode(texts, convert_to_tensor=True, show_progress_bar=True)

        try:
            print("Saving embeddings to local cache for next launch...")
            np.save(emb_path, self.reference_embeddings.cpu().numpy())
            with open(ent_path, "w", encoding="utf-8") as f:
                json.dump(self.reference_entries, f, ensure_ascii=False, indent=2)
            print("Embeddings cache successfully saved!")
        except Exception as e:
            print(f"Error saving cache: {e}")

    def _prepare_alias_embeddings(self):
        """
        Mã hóa bổ sung các alias lâm sàng đã kiểm chứng.

        Tập này nhỏ (vài trăm cụm) nên tính trong vài giây, và được tách riêng
        khỏi cache chính để việc bổ sung từ đồng nghĩa không buộc phải tính lại
        toàn bộ ~47k vector của danh mục.
        """
        import torch

        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        model_key, _ = self._cache_key()
        alias_pairs = [
            (phrase, code)
            for phrase, codes in DIRECT_ALIASES.items()
            for code in codes
            if code in self.db_index
        ]
        # Nội dung đã vá của DATA_FIXES chưa có trong cache chính, đưa vào đây.
        for code, patch in DATA_FIXES.items():
            if code not in self.db_index:
                continue
            for value in patch.values():
                if value:
                    alias_pairs.append((value, code))
        digest = hashlib.sha1(
            "|".join(f"{p}=>{c}" for p, c in alias_pairs).encode("utf-8")
        ).hexdigest()[:12]
        alias_path = os.path.join(data_dir, f"alias_embeddings_{model_key}_{digest}.npy")

        alias_entries = [
            {"code": code, "text": phrase, "type": "clinical_alias"}
            for phrase, code in alias_pairs
        ]
        if not alias_entries:
            self.alias_count = 0
            return

        if os.path.exists(alias_path):
            alias_emb = torch.from_numpy(np.load(alias_path)).to(self.model.device)
        else:
            print(f"Mã hóa {len(alias_entries)} alias lâm sàng...")
            alias_emb = self.model.encode(
                [self.normalize_text(e["text"]) for e in alias_entries],
                convert_to_tensor=True,
                show_progress_bar=False,
            )
            try:
                np.save(alias_path, alias_emb.cpu().numpy())
            except Exception as e:
                print(f"Không lưu được cache alias: {e}")

        self.alias_count = len(alias_entries)
        self.reference_entries = self.reference_entries + alias_entries
        self.reference_embeddings = torch.cat(
            [self.reference_embeddings, alias_emb.to(self.reference_embeddings.device)], dim=0
        )

    def _build_lookup_tables(self):
        """
        Tiền tính mọi cấu trúc tra cứu dùng lúc chạy.

        Phiên bản trước chuẩn hóa và sắp xếp lại toàn bộ ~47k thuật ngữ trên MỖI
        request để trích xuất thực thể. Ở đây làm đúng một lần lúc khởi tạo.
        """
        self._entry_norm: List[str] = []
        self._entry_tokens: List[frozenset] = []
        # Bảng tra n-gram: cụm chuẩn hóa -> entry, dùng cho NER thời gian hằng số.
        self._term_index: Dict[str, dict] = {}
        # Bảng tra không dấu, dùng làm phương án dự phòng vì bác sĩ thường gõ
        # bệnh án không dấu ("hen phe quan cap").
        self._term_index_ascii: Dict[str, dict] = {}
        # Từ điển khôi phục dấu: "hen phe quan" -> "hen phế quản".
        self._entry_norm_tone: List[str] = []
        self._ascii_to_norm: Dict[str, str] = {}
        _restore_priority: Dict[str, int] = {}
        _TYPE_RANK = {"clinical_alias": 3, "official_vi": 2, "synonym": 1, "official_en": 0}

        for entry in self.reference_entries:
            norm = self.normalize_text(entry["text"])
            self._entry_norm.append(norm)
            # Tính sẵn bản đã thống nhất dấu. Làm trong vòng chấm điểm thì 15 mẫu
            # regex chạy lại cho cả 400 ứng viên mỗi truy vấn - đo được độ trễ
            # trung bình 22 ms -> 93 ms, tức trả bằng hiệu năng cho một phép biến
            # đổi không bao giờ đổi kết quả.
            self._entry_norm_tone.append(thong_nhat_dau_thanh(norm))
            self._entry_tokens.append(frozenset(self._content_tokens(norm)))
            if len(norm) >= 4 and len(norm.split()) <= _MAX_NER_NGRAM:
                # Ưu tiên thuật ngữ chính thức khi nhiều mã cùng một cụm chữ.
                prev = self._term_index.get(norm)
                if prev is None or (prev["type"] != "official_vi" and entry["type"] == "official_vi"):
                    self._term_index[norm] = entry
                self._term_index_ascii.setdefault(strip_diacritics(norm), entry)

                # Chỉ nhận cụm từ 2 từ trở lên: cụm một từ quá dễ nhập nhằng khi
                # bỏ dấu ("than" vừa là "thận" vừa là "than" trong "bệnh than").
                if len(norm.split()) >= 2 and norm != strip_diacritics(norm):
                    key = strip_diacritics(norm)
                    rank = _TYPE_RANK.get(entry["type"], 0)
                    if rank > _restore_priority.get(key, -1):
                        self._ascii_to_norm[key] = norm
                        _restore_priority[key] = rank

        # Vị trí các entry theo mã, dùng để ép ứng viên alias vào vòng tái xếp hạng.
        self._code_to_indices: Dict[str, List[int]] = {}
        for idx, entry in enumerate(self.reference_entries):
            self._code_to_indices.setdefault(entry["code"], []).append(idx)

        # Nhãn trục đối lập của từng mã ICD-10, suy ra từ tên chính thức VI + EN.
        self._code_polarity: Dict[str, Dict[str, str]] = {}
        for code, entry in self.db_index.items():
            surface = self.normalize_text(f"{entry['name_vi']} {entry.get('name_en') or ''}")
            self._code_polarity[code] = self._polarity_labels(surface)

        self._alias_norm = {self.normalize_text(k): v for k, v in DIRECT_ALIASES.items()}
        self._alias_ascii = {strip_diacritics(k): v for k, v in self._alias_norm.items()}

        self._build_dagger_links()

    def _build_dagger_links(self):
        """
        Dựng bảng liên kết dao găm/sao (†/*) trực tiếp từ tên mã trong danh mục.

        ICD-10 mã hóa chẩn đoán kép bằng một CẶP: mã † cho bệnh nguyên và mã * cho
        biểu hiện. Danh mục của Bộ Y tế ghi sẵn mã đối tác ngay trong tên, nên
        không cần bảng đối chiếu chép tay:

            M51.1† "... có kèm tổn thương của rễ tủy sống (G55.1*)"
            G55.1* "Chèn ép rễ và đám rối thần kinh trong bệnh đĩa đệm (M50-M51†)"

        Tham chiếu ĐÍCH DANH (197 mã) đủ chặt để suy ra cặp; tham chiếu dạng DẢI
        (86 mã, "M50-M51†") chỉ khoanh vùng khối hợp lệ nên chỉ dùng để kiểm tra
        tính hợp lệ, không dùng để chọn mã.
        """
        self._link_exact: Dict[str, Set[str]] = {}
        self._link_range: Dict[str, List[Tuple[Tuple[str, int], Tuple[str, int]]]] = {}
        self._marked_dagger: Set[str] = set()
        self._marked_asterisk: Set[str] = set()

        for raw_code, entry in self.db_index.items():
            code = sanitize_icd10_code(raw_code)
            if "†" in raw_code:
                self._marked_dagger.add(code)
            if "*" in raw_code:
                self._marked_asterisk.add(code)

            # Nguồn đánh dấu CHÍNH: phụ lục A1 của Bộ Y tế, đã nạp sẵn vào
            # `meta.asterisk_codes` (374 mã bệnh nguyên).
            #
            # Không thể dựa vào ký tự † trong mã nữa: danh mục nay gỡ sạch † và *
            # vì máy chủ FHIR từ chối mã mang chúng. Sau lần gỡ đó, hai tập trên
            # RỖNG và mọi nhánh ghép cặp †/* thành code chết - hệ thống lặng lẽ
            # thôi nhận ra chẩn đoán kép, trong khi bảng liên kết vẫn đúng.
            for ma_sao in (entry.get("meta") or {}).get("asterisk_codes") or ():
                sach = sanitize_icd10_code(ma_sao)
                if sach in self.db_index:
                    self._marked_dagger.add(code)
                    self._marked_asterisk.add(sach)

            surface = f"{entry['name_vi']} {entry.get('name_en') or ''}"
            for match in _CODE_REF_RE.finditer(surface):
                start, end, dau = match.group(1), match.group(2), match.group(3)

                # Ký tự đánh dấu trong tên nói rõ ai là bệnh nguyên, ai là biểu
                # hiện, nên không phải đoán theo chiều tham chiếu:
                #
                #   M51.1 "... tổn thương của rễ tủy sống (G55.1*)"  -> G55.1 là *
                #   G55.1 "... trong bệnh đĩa đệm (M50-M51†)"        -> G55.1 là *
                #
                # Phụ lục A1 tuy chuẩn nhưng THIẾU: nó không có cặp M51.1/G55.1
                # trong khi tên hai mã tham chiếu nhau rõ ràng. Lấy cả hai nguồn
                # thì phủ được nhiều cặp hơn mà không nguồn nào phải đoán.
                if dau == "*":
                    self._marked_dagger.add(code)
                    if not end and sanitize_icd10_code(start) in self.db_index:
                        self._marked_asterisk.add(sanitize_icd10_code(start))
                else:
                    self._marked_asterisk.add(code)
                    if not end and sanitize_icd10_code(start) in self.db_index:
                        self._marked_dagger.add(sanitize_icd10_code(start))

                if end:
                    lo, hi = self._block_key(self._block_of(start)), self._block_key(self._block_of(end))
                    if lo and hi:
                        self._link_range.setdefault(code, []).append((lo, hi))
                    continue
                # Liên kết hai chiều: mã * thường không nhắc lại từng mã † và ngược lại.
                self._link_exact.setdefault(code, set()).add(start)
                self._link_exact.setdefault(start, set()).add(code)

    @staticmethod
    def _block_key(block: str) -> Optional[Tuple[str, int]]:
        """Khóa so sánh thứ tự của một khối ICD-10 ('M51' -> ('M', 51))."""
        match = re.match(r"^([A-Z])(\d{2})$", block)
        return (match.group(1), int(match.group(2))) if match else None

    # ------------------------------------------------------------------
    # Chuẩn hóa văn bản
    # ------------------------------------------------------------------
    def normalize_text(self, text: str) -> str:
        """
        Chuẩn hóa cơ bản: hạ chữ thường, dọn ký tự đặc biệt, giải nghĩa viết tắt.

        Hàm này được dùng cho CẢ văn bản tham chiếu lúc sinh embedding, nên mọi
        thay đổi ở đây sẽ làm cache không còn nhất quán. Viết tắt bổ sung dành
        riêng cho câu truy vấn phải đặt trong QUERY_ABBREVIATIONS.
        """
        if not text:
            return ""
        # Bắt buộc dựng lại ký tự tổ hợp thành ký tự dựng sẵn TRƯỚC khi lọc ký tự
        # đặc biệt. Danh mục Bộ Y tế có 197 mã lưu dạng NFD ("không" = k h o U+0302
        # n g); dấu tổ hợp không thuộc \w nên bị dòng dưới thay bằng dấu cách, cắt
        # "không" thành "kho ng" - mất token, mất điểm khớp trọn cụm, mất cả điểm
        # thưởng mã "không đặc hiệu". Bệnh án dán từ macOS/HIS cũng hay ở dạng NFD.
        text = unicodedata.normalize("NFC", text)
        text = text.lower().strip()
        text = re.sub(r"[^\w\s\-\/\.]", " ", text)
        text = re.sub(r"\s+", " ", text)
        for pattern, replacement in _ABBR_RE:
            text = pattern.sub(replacement, text)
        return text.strip()

    def expand_query(self, text: str, with_bridges: bool = True) -> str:
        """
        Chuẩn hóa dành riêng cho câu chẩn đoán của bác sĩ.

        Ngoài normalize_text còn: bỏ tiền tố hành văn ("bệnh nhân bị", "chẩn đoán:"),
        giải nghĩa bộ viết tắt mở rộng, và nối thêm cụm thuật ngữ chuẩn của danh
        mục ICD-10. Bước nối là mấu chốt: bác sĩ viết "tuýp 2" nhưng danh mục
        Bộ Y tế ghi "không phụ thuộc insuline" - hai cụm không hề chung từ nào,
        nên nếu không bắc cầu thì embedding sẽ kéo về nhóm E10 (tuýp 1).

        `with_bridges=False` dùng cho trích xuất thực thể, để chỉ bôi màu những
        cụm bác sĩ thực sự viết ra chứ không bôi cả cụm do hệ thống nối thêm.
        """
        normalized = self.normalize_text(text)
        if not normalized:
            return ""

        # Bóc lặp: các tiền tố hành văn thường xếp chồng nhau, quét một lượt duy
        # nhất sẽ bỏ sót ("Hiện tại | bệnh nhân được chẩn đoán: | ...").
        for _ in range(4):
            before = normalized
            for pattern in _NOISE_RE:
                normalized = pattern.sub("", normalized).strip()
            if normalized == before:
                break
        for pattern in _NOISE_SUFFIX_RE:
            normalized = pattern.sub("", normalized).strip()
        for pattern, replacement in _QUERY_ABBR_RE:
            normalized = pattern.sub(replacement, normalized)
        normalized = self.restore_diacritics(normalized)
        # Khôi phục dấu có thể làm lộ ra viết tắt mới, nên chạy lại một lượt.
        for pattern, replacement in _QUERY_ABBR_RE:
            normalized = pattern.sub(replacement, normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        if with_bridges:
            bridges = [std for term, std in BRIDGE_TERMS
                       if term in normalized and std not in normalized]
            if bridges:
                normalized = normalized + " " + " ".join(bridges)
        return normalized.strip()

    def restore_diacritics(self, text: str) -> str:
        """
        Khôi phục dấu tiếng Việt cho các cụm gõ không dấu, dựa trên từ điển thuật
        ngữ y khoa đã dựng từ danh mục ICD-10.

        Bác sĩ thường gõ nhanh không dấu ("nhoi mau co tim cap"). Mô hình SBERT
        coi chuỗi không dấu là từ hoàn toàn khác nên trả về kết quả ngẫu nhiên
        (chuỗi trên từng cho ra "Nhịp nhanh kịch phát"). Bước này quét n-gram dài
        nhất trước và chỉ thay thế những cụm vốn KHÔNG có dấu, nên văn bản gõ đúng
        chính tả không bị đụng tới.
        """
        table = getattr(self, "_ascii_to_norm", None)
        if not table or not text:
            return text

        words = text.split()
        out, i, n = [], 0, len(words)
        while i < n:
            matched = False
            for length in range(min(_MAX_NER_NGRAM, n - i), 1, -1):
                phrase = " ".join(words[i:i + length])
                # Chỉ xét cụm chưa có dấu; cụm đã có dấu coi như bác sĩ gõ đúng.
                if phrase != strip_diacritics(phrase):
                    continue
                restored = table.get(phrase)
                if restored:
                    out.append(restored)
                    i += length
                    matched = True
                    break
            if not matched:
                out.append(words[i])
                i += 1
        return " ".join(out)

    @staticmethod
    def _content_tokens(text: str) -> List[str]:
        """
        Tách token nội dung, quy hai lối đặt dấu trên oa/oe/uy về một.

        Thống nhất dấu ở ĐÂY chứ không ở `normalize_text`: hàm kia nuôi cả văn
        bản sinh embedding, mà mô hình được fine-tune trên dạng chữ của danh mục
        gốc. Đổi chuẩn hóa ở đó là đẩy vector tham chiếu lệch khỏi thứ mô hình đã
        học - đo được: Top-1 holdout tụt 72,5% -> 70,6%, nhóm polarity trên tập
        phát triển tụt 94,4% -> 83,3%.

        Tầng embedding vốn đã chịu được lối gõ khác nhờ các biến thể không dấu.
        Chỗ thật sự gãy là so khớp token - "thùy" và "thuỳ" là hai chuỗi khác
        nhau nên điểm khớp từ vựng mất trắng. Sửa đúng tầng đó thôi.
        """
        text = thong_nhat_dau_thanh(text)
        return [t for t in text.split() if t not in STOPWORDS and len(t) > 1]

    # ------------------------------------------------------------------
    # Trục đối lập ngữ nghĩa
    # ------------------------------------------------------------------
    @staticmethod
    def _polarity_labels(surface: str) -> Dict[str, str]:
        """Gán nhãn cho từng trục đối lập. Nhãn khớp đầu tiên thắng."""
        labels = {}
        for axis in POLARITY_AXES:
            if not any(s in surface for s in axis["scope"]):
                continue
            for label, markers in axis["labels"]:
                if any(m in surface for m in markers):
                    labels[axis["name"]] = label
                    break
        return labels

    def _polarity_delta(self, q_labels: Dict[str, str], code: str) -> Tuple[float, List[str]]:
        """Cộng/trừ điểm theo mức khớp nhãn giữa câu truy vấn và mã ứng viên."""
        delta, notes = 0.0, []
        c_labels = self._code_polarity.get(code, {})
        for axis in POLARITY_AXES:
            name = axis["name"]
            q, c = q_labels.get(name), c_labels.get(name)
            if not q or not c:
                continue
            if q == c:
                delta += axis["bonus"]
            else:
                delta -= axis["penalty"]
                notes.append(f"xung đột {name}: chẩn đoán '{q}' vs mã '{c}'")
        return delta, notes

    def _polarity_compatible(self, q_labels: Dict[str, str], code: str) -> bool:
        """
        Mã có khẳng định điều gì mà câu chẩn đoán không hề nói tới không.

        Dùng làm chốt cho sàn tin cậy theo độ phủ. Độ phủ đếm theo túi từ nên mù
        với hai kiểu sai nguy hiểm, cả hai đều từng lọt lên mức "tự động liên
        thông" trong lần đo trước:

          - Mã khẳng định thêm: câu "phình động mạch chủ bụng" không nói vỡ hay
            không, mã I71.3 lại là "Phình động mạch chủ bụng, vỡ". Đủ từ nhưng mã
            tự thêm một tình trạng cấp cứu mà bác sĩ chưa hề ghi.
          - Phủ định gắn sai chỗ: câu "viêm mũi không dị ứng" và tên mã J30.4
            "Viêm mũi dị ứng, không phân loại" dùng chung đúng bấy nhiêu từ, chỉ
            khác chỗ đặt chữ "không" - mà nghĩa thì ngược hẳn nhau.

        Nguyên tắc: trục nào mã khẳng định thì câu phải khẳng định y hệt. Câu im
        lặng ở trục đó cũng không đủ điều kiện - im lặng không phải là đồng ý.
        """
        c_labels = self._code_polarity.get(code, {})
        for axis in POLARITY_AXES:
            name = axis["name"]
            code_label = c_labels.get(name)
            if code_label and q_labels.get(name) != code_label:
                return False
        return True

    @staticmethod
    def _specificity_delta(q_tokens: frozenset, entry_norm: str,
                           entry_tokens: frozenset) -> Tuple[float, List[str]]:
        """
        Điều chỉnh theo mức chi tiết của mã.

        "bệnh Crohn" (không nêu thể) phải ra K50.9 "không đặc hiệu", chứ không phải
        K50.8 "Bệnh Crohn khác". Ngược lại "viêm kết mạc dị ứng" (có nêu thể) không
        được ra H10.9 "không đặc hiệu" vì mã đó đánh rơi vế "dị ứng".
        """
        is_unspecified = any(m in entry_norm for m in UNSPECIFIED_MARKERS)
        is_other = any(m in entry_norm.split() for m in OTHER_MARKERS)
        if not (is_unspecified or is_other):
            return 0.0, []

        # Câu chẩn đoán không mang thông tin nào ngoài tên bệnh của chính mã này.
        query_is_unqualified = q_tokens <= entry_tokens

        if is_unspecified:
            if query_is_unqualified:
                return UNSPECIFIED_BONUS, ["chẩn đoán không nêu thể bệnh -> ưu tiên mã không đặc hiệu"]
            return -UNSPECIFIED_PENALTY, ["chẩn đoán có nêu thể bệnh -> mã không đặc hiệu bị hạ bậc"]
        if query_is_unqualified:
            return -OTHER_PENALTY, ["chẩn đoán không nêu thể bệnh -> hạ bậc mã 'khác'"]
        return 0.0, []

    @staticmethod
    def _chapter_delta(query: str, code: str) -> Tuple[float, List[str]]:
        """Chặn các chương ICD-10 chỉ hợp lệ trong ngữ cảnh chuyên biệt."""
        for gate in CHAPTER_GATES:
            if not code.upper().startswith(gate["prefixes"]):
                continue
            if not any(kw in query for kw in gate["required_any"]):
                return -gate["penalty"], [f"chương {code[0]} cần ngữ cảnh chuyên biệt"]
            break
        return 0.0, []

    def _alias_targets(self, query: str) -> Dict[str, float]:
        """
        Tìm alias lâm sàng xuất hiện trong câu chẩn đoán -> điểm thưởng theo mã.

        So khớp hai vòng: có dấu trước, không dấu sau (hệ số thấp hơn vì bỏ dấu
        làm tăng nguy cơ khớp nhầm).
        """
        boosts: Dict[str, float] = {}
        query_tokens = max(1, len(self._content_tokens(query)))

        def apply(table: Dict[str, List[str]], haystack: str, factor: float):
            for phrase, codes in table.items():
                if not phrase or phrase not in haystack:
                    continue
                # Alias chỉ phủ một phần câu chẩn đoán là bằng chứng yếu hơn:
                # "hen phế quản" trong "hen phế quản không dị ứng" không được phép
                # lấn át mã J45.1 vốn mô tả đúng cả vế "không dị ứng".
                coverage = min(1.0, len(self._content_tokens(phrase)) / query_tokens)
                for rank, code in enumerate(codes):
                    if code not in self.db_index:
                        continue
                    bonus = ALIAS_BONUS_PRIMARY if rank == 0 else ALIAS_BONUS_SECONDARY
                    boosts[code] = max(boosts.get(code, 0.0), bonus * factor * coverage)

        apply(self._alias_norm, query, 1.0)
        apply(self._alias_ascii, strip_diacritics(query), 0.85)
        return boosts

    @staticmethod
    def _lexical_f1(q_tokens: frozenset, e_tokens: frozenset) -> float:
        if not q_tokens or not e_tokens:
            return 0.0
        inter = len(q_tokens & e_tokens)
        if not inter:
            return 0.0
        precision = inter / len(e_tokens)
        recall = inter / len(q_tokens)
        return 2 * precision * recall / (precision + recall)

    @staticmethod
    def _block_of(code: str) -> str:
        """Khối ICD-10 3 ký tự của một mã (E11.9 -> E11)."""
        return sanitize_icd10_code(code).split(".")[0]

    @staticmethod
    def _calibrate(relative: float, similarity: float, coverage: float = 0.0) -> float:
        """
        Quy đổi (xác suất tương đối, độ tương đồng thô, độ phủ) sang độ tin cậy %.

        Thành phần `relative` đo mức áp đảo so với ứng viên khác, và nó chiếm 65%
        điểm số. Điều đó khiến một câu khớp gần như tuyệt đối vẫn bị điểm thấp chỉ
        vì trong danh mục còn vài mã lân cận: "bệnh van hai lá do thấp" khớp I05
        "Bệnh lý van hai lá do thấp" với cosine 0,991 - tức gần như trùng nghĩa
        hoàn toàn - nhưng chỉ được 56,26% vì các mã van tim khác chia mất xác suất.
        Bác sĩ gõ đúng tên bệnh mà máy vẫn báo "cần duyệt lại" là phản trực giác.

        Gộp khối 3 ký tự đã xử lý phần anh em cùng mã cha (I05 với I05.1), nhưng
        không xử lý được các khối lân cận về mặt lâm sàng (I05 với I08, I34).

        Nên bổ sung sàn theo độ phủ: khi mọi từ mang nghĩa trong câu đều xuất hiện
        ở tên mã VÀ độ tương đồng thô rất cao, đó là khớp trọn nghĩa - không để
        yếu tố cạnh tranh kéo xuống dưới sàn.

        Độ phủ cũng chặn được chiều ngược lại, thứ mà cosine không chặn nổi:
        "viêm kết mạc dị ứng" khớp H10.9 "Viêm kết mạc, không đặc hiệu" ở cosine
        0,953 nhưng thiếu hẳn hai từ "dị ứng" - đúng phần mang nghĩa phân biệt.
        Độ phủ chưa trọn thì không được hưởng sàn.
        """
        absolute = (similarity - SIM_FLOOR) / (SIM_CEIL - SIM_FLOOR)
        absolute = max(0.0, min(1.0, absolute))
        blended = W_RELATIVE * relative + W_ABSOLUTE * absolute
        if coverage >= COVERAGE_FULL and similarity >= COVERAGE_FLOOR_SIM:
            blended = max(blended, COVERAGE_FLOOR_CONF)
        return round(max(0.0, min(1.0, blended)) * 100, 2)

    # ------------------------------------------------------------------
    # Trích xuất thực thể (NER theo luật)
    # ------------------------------------------------------------------
    def extract_entities_regex(self, text: str):
        """
        Dò thuật ngữ y khoa bằng cách quét n-gram trên bảng tra đã tiền tính.

        Độ phức tạp O(số từ × độ dài n-gram tối đa) thay vì O(số thuật ngữ) như
        trước, nên không còn phụ thuộc vào kích thước danh mục.
        """
        normalized = self.expand_query(text, with_bridges=False)
        if not normalized:
            return []

        words = normalized.split()
        n = len(words)
        matches: List[Tuple[int, int, dict]] = []

        for start in range(n):
            for length in range(min(_MAX_NER_NGRAM, n - start), 0, -1):
                phrase = " ".join(words[start:start + length])
                entry = self._term_index.get(phrase)
                if entry is None and len(phrase) >= 6:
                    entry = self._term_index_ascii.get(strip_diacritics(phrase))
                if entry is not None:
                    matches.append((start, start + length, entry))
                    break  # cụm dài nhất tại vị trí này đã khớp

        # Giữ cụm dài nhất, loại các cụm chồng lấn ngắn hơn.
        matches.sort(key=lambda m: (m[0] - m[1], m[0]))
        taken: List[Tuple[int, int]] = []
        entities = []
        for start, end, entry in matches:
            if any(not (end <= s or start >= e) for s, e in taken):
                continue
            taken.append((start, end))
            phrase = " ".join(words[start:end])
            entities.append({
                "text": self._surface_form(text, phrase) or phrase,
                "normalized": phrase,
                "code": sanitize_icd10_code(entry["code"]),
                "code_no_dot": code_no_dot(entry["code"]),
                "type": entry["type"],
            })

        entities.sort(key=lambda e: normalized.find(e["normalized"]))
        return entities

    @staticmethod
    def _surface_form(original: str, phrase: str) -> Optional[str]:
        """
        Tìm đoạn văn bản gốc tương ứng với cụm đã chuẩn hóa.

        Bản trước cắt `text[start:end]` bằng chỉ số tính trên chuỗi ĐÃ chuẩn hóa,
        chỉ đúng khi hai chuỗi tình cờ dài bằng nhau. Ở đây so khớp không phân
        biệt dấu, và trả None nếu không tìm được để phía gọi tự chọn phương án dự phòng.
        """
        if not phrase:
            return None
        haystack = strip_diacritics(original.lower())
        needle = strip_diacritics(phrase.lower())
        pos = haystack.find(needle)
        if pos == -1:
            return None
        return original[pos:pos + len(needle)]

    # ------------------------------------------------------------------
    # Truy vấn
    # ------------------------------------------------------------------
    def query(self, user_query: str, top_k: int = 4,
              patient_sex: Optional[str] = None,
              patient_birth_date: Optional[str] = None,
              patient_age_days: Optional[int] = None) -> List[dict]:
        """
        Trả về danh sách mã ICD-10 ứng viên đã tái xếp hạng và hiệu chuẩn.

        `patient_sex` ("male"/"female") và tuổi bệnh nhân là TÙY CHỌN. Có thì ứng
        viên sai về mặt lâm sàng bị hạ bậc theo phụ lục A2/A3/A4 của Bộ Y tế -
        bệnh nhân nam không nhận mã sản khoa, người 60 tuổi không nhận mã sơ sinh.
        Không có thì hành vi y hệt trước đây, nên mọi đường gọi cũ không đổi.
        """
        if patient_age_days is None:
            patient_age_days = tuoi_theo_ngay(patient_birth_date)

        expanded = self.expand_query(user_query)
        if not expanded:
            return []

        query_embedding = self.model.encode(expanded, convert_to_tensor=True)
        cos_scores = util.cos_sim(query_embedding, self.reference_embeddings)[0].cpu().numpy()

        q_labels = self._polarity_labels(expanded)
        q_tokens = frozenset(self._content_tokens(expanded))
        # Một lần cho cả truy vấn, thay vì lặp lại ở từng ứng viên.
        expanded_tone = thong_nhat_dau_thanh(expanded)
        alias_boosts = self._alias_targets(expanded)

        pool_size = min(CANDIDATE_POOL, len(cos_scores))
        pool = set(np.argpartition(-cos_scores, pool_size - 1)[:pool_size].tolist())
        # Mã trúng alias luôn được xét, kể cả khi cosine xếp ngoài pool.
        for code in alias_boosts:
            pool.update(self._code_to_indices.get(code, []))

        best: Dict[str, dict] = {}
        for idx in pool:
            entry = self.reference_entries[idx]
            code = entry["code"]

            score = float(cos_scores[idx])
            notes: List[str] = []

            score += W_LEXICAL * self._lexical_f1(q_tokens, self._entry_tokens[idx])

            entry_norm = self._entry_norm[idx]
            # Cùng lý do với `_content_tokens`: so chuỗi thô thì "viêm phổi thuỳ"
            # không nằm trong "viêm phổi thùy", mất luôn điểm khớp trọn cụm.
            if (entry_norm and len(entry_norm) >= 5
                    and self._entry_norm_tone[idx] in expanded_tone):
                score += W_EXACT
                notes.append(f"khớp trọn cụm '{entry_norm}'")

            if code in alias_boosts:
                score += alias_boosts[code]
                notes.append("khớp alias lâm sàng đã kiểm chứng")

            polarity_delta, polarity_notes = self._polarity_delta(q_labels, code)
            score += polarity_delta
            notes.extend(polarity_notes)

            spec_delta, spec_notes = self._specificity_delta(
                q_tokens, entry_norm, self._entry_tokens[idx])
            score += spec_delta
            notes.extend(spec_notes)

            chapter_delta, chapter_notes = self._chapter_delta(expanded, code)
            score += chapter_delta
            notes.extend(chapter_notes)

            # Ràng buộc lâm sàng theo phụ lục Bộ Y tế. Đây là thứ độ tương đồng
            # văn bản không bao giờ bắt được: "viêm tinh hoàn" và "viêm buồng
            # trứng" giống nhau về mặt chữ hơn hẳn về mặt người bệnh.
            cons_delta, cons_notes = rang_buoc_lam_sang(
                self.db_index[code].get("meta"), patient_sex, patient_age_days)
            score += cons_delta
            notes.extend(cons_notes)

            # Tỉ lệ từ trong câu hỏi được tên mã phủ. Giữ lại để khâu hiệu chuẩn
            # phân biệt "khớp trọn nghĩa" với "khớp phần đầu rồi bỏ mất chi tiết".
            coverage = (len(q_tokens & self._entry_tokens[idx]) / len(q_tokens)
                        if q_tokens else 0.0)

            current = best.get(code)
            if current is None or score > current["score"]:
                best[code] = {
                    "score": score,
                    "raw": float(cos_scores[idx]),
                    "coverage": coverage,
                    "matched_text": entry["text"],
                    "match_type": entry["type"],
                    "notes": notes,
                }

        ranked = sorted(best.items(), key=lambda kv: kv[1]["score"], reverse=True)

        # Chuẩn hóa softmax trên nhóm dẫn đầu để lấy xác suất tương đối.
        scope = ranked[:SOFTMAX_SCOPE]
        probs: List[float] = []
        block_mass: Dict[str, float] = {}
        if scope:
            top_score = scope[0][1]["score"]
            exps = np.exp([(kv[1]["score"] - top_score) / SOFTMAX_TEMPERATURE for kv in scope])
            probs = (exps / exps.sum()).tolist()
            # E11 và E11.9 là cùng một chẩn đoán ở hai mức chi tiết. Nếu để chúng
            # chia đôi xác suất thì độ tin cậy bị hạ oan, nên gộp khối 3 ký tự lại.
            for (code, _), p in zip(scope, probs):
                block_mass[self._block_of(code)] = block_mass.get(self._block_of(code), 0.0) + p

        results = []
        claimed_blocks = set()
        for rank, (code, info) in enumerate(ranked[:top_k]):
            db_entry = self.db_index[code]
            block = self._block_of(code)
            if rank < len(probs):
                # Mã tốt nhất trong khối nhận toàn bộ khối lượng xác suất của khối.
                if block in claimed_blocks:
                    relative = probs[rank]
                else:
                    relative = block_mass.get(block, probs[rank])
                    claimed_blocks.add(block)
            else:
                relative = 0.0
            # Chỉ ca khớp trọn nghĩa VÀ không xung đột phủ định mới được hưởng sàn.
            eligible = (info.get("coverage", 0.0)
                        if self._polarity_compatible(q_labels, code) else 0.0)
            confidence = self._calibrate(relative, info["raw"], eligible)
            results.append({
                "code": sanitize_icd10_code(code),
                "code_no_dot": code_no_dot(code),
                "raw_code": code,
                "name_vi": db_entry["name_vi"],
                "name_en": db_entry.get("name_en") or "",
                "confidence": confidence,
                "confidence_band": confidence_band(confidence),
                "similarity_score": round(info["raw"], 4),
                "rerank_score": round(info["score"], 4),
                "matched_by": info["matched_text"],
                "match_type": info["match_type"],
                "explanation": info["notes"],
            })
        return results

    # ------------------------------------------------------------------
    # Chẩn đoán kép (†/*)
    # ------------------------------------------------------------------
    def split_clinical_fragments(self, text: str) -> List[str]:
        """
        Tách một dòng chẩn đoán thành các vế lâm sàng độc lập.

        Cần thiết vì khi mã hóa cả câu thành MỘT vector, vế phụ bị vế chính lấn át:
        "Thoát vị đĩa đệm cột sống, chèn rễ dây thần kinh" đẩy G55.1* xuống hạng 5,
        nhưng riêng vế "chèn rễ dây thần kinh" thì G55.1* đứng đầu với 67%.
        """
        parts = []
        for raw in _FRAGMENT_SPLIT_RE.split(text or ""):
            fragment = raw.strip(" .-\t")
            # Bỏ vế quá ngắn: "cấp", "(P)" ... không đủ ngữ nghĩa để truy vấn riêng.
            if len(fragment) >= 6 and len(fragment.split()) >= 2:
                parts.append(fragment)
            if len(parts) >= MAX_FRAGMENTS:
                break
        return parts

    def _is_group_label(self, code: str) -> bool:
        """
        Tên mã có phải nhãn chỉ mang nghĩa khi đặt dưới nhóm cha không.

        D16.6 tên là "Cột sống" nằm dưới nhóm "U lành của xương và sụn khớp":
        bản thân "cột sống" là vị trí giải phẫu, không phải một chẩn đoán. Dùng
        để loại những cụm như vậy khỏi việc tách một vế thành nhiều bệnh.
        """
        entry = next(
            (self.db_index[c] for c in (code, f"{code}†", f"{code}*") if c in self.db_index),
            None)
        if entry is None:
            return False

        parent = (entry.get("meta") or {}).get("type_name") or ""
        return bool(_NEOPLASM_RE.search(parent.lower())
                    and not _NEOPLASM_RE.search(entry["name_vi"].lower()))

    def _term_fragments(self, fragment: str) -> List[str]:
        """
        Các tên bệnh của danh mục nằm trong MỘT vế, khi vế đó chứa từ hai bệnh.

        Cắt theo dấu câu bỏ sót trường hợp bác sĩ viết hai bệnh liền nhau không có
        dấu phân cách: "đái tháo đường tuýp 2 tăng huyết áp" là một vế duy nhất,
        mã hóa cả cụm thành một vector thì E11 và I10 chia nhau xác suất và không
        mã nào đạt ngưỡng duyệt.

        Chỉ nhận cụm THỰC SỰ có trong danh mục ICD-10, nên phần chữ không phải
        chẩn đoán ("đã điều trị 3 ngày", "theo dõi thêm") tự bị bỏ qua - không
        cần cắt câu theo hành văn rồi tra những vế vô nghĩa.

        Trả [] khi vế chỉ chứa một bệnh, để phía gọi giữ nguyên vế gốc: vế đầy đủ
        mang nhiều thông tin lâm sàng hơn nên cho mã chi tiết hơn tên bệnh trần.
        """
        seen_blocks, terms = set(), []
        for entity in self.extract_entities_regex(fragment):
            phrase = entity["normalized"]
            if len(phrase) < MIN_TERM_FRAGMENT_LEN or _CODE_LIKE_RE.match(phrase):
                continue
            if self._is_group_label(entity["code"]):
                continue
            # Hai thuật ngữ cùng khối ICD-10 là hai cách gọi một bệnh, không phải
            # hai chẩn đoán. Giữ cụm đầu tiên (NER đã ưu tiên cụm dài nhất).
            block = self._block_of(entity["code"])
            if block in seen_blocks:
                continue
            seen_blocks.add(block)
            terms.append(entity["text"] or phrase)
        return terms if len(terms) >= 2 else []

    def _candidate_fragments(self, text: str) -> List[str]:
        """Vế cắt theo dấu câu, vế nào gộp nhiều bệnh thì tách tiếp theo tên bệnh."""
        fragments = self.split_clinical_fragments(text) or [text]

        expanded: List[str] = []
        for fragment in fragments:
            expanded.extend(self._term_fragments(fragment) or [fragment])

        # Khử trùng lặp nhưng giữ thứ tự xuất hiện trong câu.
        unique = list(dict.fromkeys(f for f in expanded if f.strip()))
        return unique[:MAX_FRAGMENTS]

    def _is_valid_pair(self, etiology: str, manifestation: str) -> bool:
        """Cặp (bệnh nguyên †, biểu hiện *) có hợp lệ theo danh mục không."""
        if etiology == manifestation:
            return False
        if manifestation not in self._marked_asterisk:
            return False
        if etiology in self._link_exact.get(manifestation, ()):
            return True
        # Mã * thường chỉ khoanh dải bệnh nguyên ("M50-M51†") thay vì liệt kê từng mã.
        key = self._block_key(self._block_of(etiology))
        if key is None:
            return False
        return any(lo <= key <= hi for lo, hi in self._link_range.get(manifestation, ()))

    def _prefer_dagger_sibling(self, etiology: str, manifestation: str) -> Optional[str]:
        """
        Đổi mã bệnh nguyên sang mã anh em có dấu † trỏ đích danh mã biểu hiện.

        Đây là định nghĩa của mã †, không phải luật chỉnh tay: khi biểu hiện đã
        được xác nhận (G55.1* - chèn ép rễ), thì trong khối M51 phải chọn M51.1†
        "có kèm tổn thương rễ tủy sống" chứ không phải M51.2 "đặc hiệu khác".
        """
        block = self._block_of(etiology)
        for candidate in self._link_exact.get(manifestation, ()):
            if (candidate != etiology
                    and candidate in self._marked_dagger
                    and self._block_of(candidate) == block):
                return candidate
        return None

    def _prediction_for(self, code: str) -> Optional[dict]:
        """Dựng bản ghi kết quả cho một mã được luật †/* kéo vào, dù nó ngoài top-k."""
        raw_code = next(
            (c for c in (code, f"{code}†", f"{code}*") if c in self.db_index), None)
        if raw_code is None:
            return None
        entry = self.db_index[raw_code]
        return {
            "code": code,
            "code_no_dot": code_no_dot(code),
            "raw_code": raw_code,
            "name_vi": entry["name_vi"],
            "name_en": entry.get("name_en") or "",
            "confidence": 0.0,
            "confidence_band": confidence_band(0.0),
            "similarity_score": 0.0,
            "rerank_score": 0.0,
            "matched_by": entry["name_vi"],
            "match_type": "dagger_asterisk",
            "explanation": [],
        }

    def query_composite(self, user_query: str, top_k: int = 4,
                        patient_sex: Optional[str] = None,
                        patient_birth_date: Optional[str] = None) -> dict:
        """
        Truy vấn có nhận diện chẩn đoán kép †/*.

        Trả về danh sách top-k phẳng như `query()` (giữ nguyên hợp đồng với phía
        gọi) kèm trường `combination` mô tả cặp mã khi phát hiện được.

        Độ tin cậy của hai mã KHÔNG cộng vào nhau: chúng cùng đến từ một câu nên
        không phải hai bằng chứng độc lập, cộng lại là đếm trùng. Cái được cộng là
        bằng chứng - một cặp †/* hợp lệ mới là lý do để nâng hạng mã bệnh nguyên.
        """
        predictions = self.query(user_query, top_k=top_k, patient_sex=patient_sex,
                                 patient_birth_date=patient_birth_date)
        fragments = self._candidate_fragments(user_query)

        # Ứng viên gộp từ cả câu lẫn từng vế, giữ bản ghi có độ tin cậy cao nhất.
        pool: Dict[str, dict] = {}
        origin: Dict[str, str] = {}
        fragment_results: Dict[str, List[dict]] = {}
        sources = [(user_query, predictions)]
        if len(fragments) >= 2:
            for fragment in fragments:
                fragment_results[fragment] = self.query(
                    fragment, top_k=FRAGMENT_TOP_K, patient_sex=patient_sex,
                    patient_birth_date=patient_birth_date)
                sources.append((fragment, fragment_results[fragment]))
        for source, items in sources:
            for item in items:
                current = pool.get(item["code"])
                if current is None or item["confidence"] > current["confidence"]:
                    pool[item["code"]] = item
                    origin[item["code"]] = source

        pairs = []
        for manifestation in pool:
            for etiology in pool:
                if self._is_valid_pair(etiology, manifestation):
                    pairs.append((
                        pool[etiology]["confidence"] + pool[manifestation]["confidence"],
                        etiology, manifestation,
                    ))
        # Câu một vế vẫn có thể ra mã †: bản thân mã † là chẩn đoán CHƯA đủ theo
        # ICD-10, phải kèm mã * biểu hiện, nên bổ sung nốt vế còn thiếu.
        if not pairs:
            for code, record in pool.items():
                if code not in self._marked_dagger:
                    continue
                partner = next(
                    (p for p in self._link_exact.get(code, ()) if p in self._marked_asterisk), None)
                if partner:
                    pairs.append((record["confidence"], code, partner))
                    origin.setdefault(partner, f"mã đi kèm bắt buộc của {code}†")
        if not pairs:
            # Không ghép được cặp †/* thì câu nhiều vế mới được xét như nhiều
            # bệnh độc lập. Thứ tự này là bắt buộc: ở ca "Thoát vị đĩa đệm cột
            # sống, chèn ép rễ thần kinh" thì vế sau CŨNG khớp mã riêng rất
            # mạnh (G55.1), nên nếu xét tách trước thì một chẩn đoán ghép sẽ bị
            # xé thành hai bệnh không có thật.
            separate = self._separate_diagnoses(fragments, fragment_results)
            if separate:
                return {
                    "predictions": self._flatten_diagnoses(separate, top_k),
                    "combination": None,
                    "diagnoses": separate,
                }
            return {
                "predictions": predictions,
                "combination": None,
                "diagnoses": [{"fragment": user_query, "predictions": predictions}],
            }

        _, etiology, manifestation = max(pairs)
        rationale = [
            f"'{origin[manifestation]}' → {manifestation} (biểu hiện *)",
            f"'{origin.get(etiology, user_query)}' → {etiology} (bệnh nguyên)",
        ]

        preferred = self._prefer_dagger_sibling(etiology, manifestation)
        if preferred:
            rationale.append(
                f"{preferred}† là mã cùng khối có dấu † trỏ đích danh {manifestation}, "
                f"nên thay cho {etiology}")

        members = []
        for code, role in ((preferred or etiology, "etiology"), (manifestation, "manifestation")):
            record = pool.get(code) or self._prediction_for(code)
            if record is None:
                return {"predictions": predictions, "combination": None}
            if preferred and code == preferred and not pool.get(code):
                # Mã † thay thế không tự đạt điểm cao (nó tả cả hai vế nên không
                # khớp trọn vế nào). Lấy min của hai vế: một chẩn đoán ghép chỉ
                # chắc chắn bằng vế yếu nhất của nó - và KHÔNG cộng hai vế lại,
                # vì chúng đến từ cùng một câu nên không phải bằng chứng độc lập.
                record = dict(record)
                record["confidence"] = round(
                    min(pool[etiology]["confidence"], pool[manifestation]["confidence"]), 2)
                record["confidence_band"] = confidence_band(record["confidence"])
                record["explanation"] = [
                    f"chọn theo luật †/*: kế thừa từ {etiology} "
                    f"({pool[etiology]['confidence']}%) và {manifestation} "
                    f"({pool[manifestation]['confidence']}%)"
                ]
            members.append({
                "code": code,
                "code_no_dot": code_no_dot(code),
                "raw_code": record["raw_code"],
                "name_vi": record["name_vi"],
                "confidence": record["confidence"],
                "role": role,
            })
            # Cặp †/* là câu trả lời được khuyến nghị, nên đưa lên đầu danh sách
            # phẳng: mã do luật kéo vào có thể vốn nằm ngoài top-k của cả câu.
            predictions = [p for p in predictions if p["code"] != code]
            predictions.append(record)

        # Hai vòng lặp trên đẩy lần lượt từng thành viên xuống cuối; đảo lại để
        # bệnh nguyên đứng trước biểu hiện, rồi mới tới các ứng viên còn lại.
        members_codes = [m["code"] for m in members]
        head = sorted((p for p in predictions if p["code"] in members_codes),
                      key=lambda p: members_codes.index(p["code"]))
        predictions = head + [p for p in predictions if p["code"] not in members_codes]

        return {
            "predictions": predictions,
            "combination": {
                "display": " ".join(m["raw_code"] for m in members),
                "members": members,
                "rationale": rationale,
            },
            # Cặp †/* là MỘT chẩn đoán được diễn đạt bằng hai mã, không phải hai
            # bệnh, nên vẫn chỉ có một mục ở đây.
            "diagnoses": [{"fragment": user_query, "predictions": predictions}],
        }

    def _separate_diagnoses(
        self, fragments: List[str], fragment_results: Dict[str, List[dict]]
    ) -> List[dict]:
        """
        Xét các vế của một dòng chẩn đoán xem có phải nhiều bệnh độc lập không.

        Chấm điểm từng vế trong pool ứng viên RIÊNG của nó, nên độ tin cậy không
        bị chia đôi: "sỏi bàng quang, suy thận cấp" cho N21.0 và N17.9 giữ nguyên
        mức 91% và 99% thay vì tụt xuống 61% và 38% như khi mã hóa cả câu thành
        một vector. Cách cũ còn sinh mã ma: "bàng quang" ở vế đầu trộn với "cấp"
        ở vế sau đẩy N30.0 "Viêm bàng quang cấp" lên hạng hai dù không ai chẩn
        đoán viêm bàng quang.

        Trả về [] khi không đủ căn cứ, để phía gọi giữ nguyên kết quả cả câu.
        """
        if len(fragments) < 2:
            return []

        diagnoses = []
        claimed_blocks = set()
        for fragment in fragments:
            results = fragment_results.get(fragment) or []
            if not results:
                continue
            best = results[0]
            if best["confidence"] < MULTI_DIAGNOSIS_MIN_CONF:
                continue
            # Hai vế cùng khối ICD-10 là hai cách nói về một bệnh ("suy thận cấp,
            # vô niệu" đều rơi vào N17), không phải hai chẩn đoán.
            block = self._block_of(best["code"])
            if block in claimed_blocks:
                continue
            claimed_blocks.add(block)
            diagnoses.append({"fragment": fragment, "predictions": results})

        return diagnoses if len(diagnoses) >= 2 else []

    @staticmethod
    def _flatten_diagnoses(diagnoses: List[dict], top_k: int) -> List[dict]:
        """
        Dựng danh sách phẳng từ nhiều chẩn đoán, giữ hợp đồng cũ với phía gọi.

        Mã chính của từng chẩn đoán lên đầu theo đúng thứ tự xuất hiện trong câu,
        rồi mới tới các ứng viên còn lại. Danh sách chỉ lấy từ kết quả của từng
        vế, KHÔNG trộn lại kết quả cả câu, vì đó chính là nguồn sinh mã ma.
        """
        flat, seen = [], set()

        def take(record: dict):
            if record["code"] in seen:
                return
            seen.add(record["code"])
            flat.append(record)

        for diagnosis in diagnoses:
            take(diagnosis["predictions"][0])

        limit = max(top_k, len(flat))
        for rank in range(1, FRAGMENT_TOP_K):
            for diagnosis in diagnoses:
                if len(flat) >= limit:
                    return flat
                if rank < len(diagnosis["predictions"]):
                    take(diagnosis["predictions"][rank])
        return flat


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    engine = NLPEngine()
    for q in ["Bệnh nhân bị ĐTĐ typ 2", "tăng huyết áp vô căn", "NMCT cấp", "hen phe quan cap"]:
        print(f"\nQuery: '{q}'  ->  '{engine.expand_query(q)}'")
        for r in engine.query(q):
            print(f" -> {r['code']}: {r['name_vi']} "
                  f"(Conf: {r['confidence']}%, Match: '{r['matched_by']}')")
