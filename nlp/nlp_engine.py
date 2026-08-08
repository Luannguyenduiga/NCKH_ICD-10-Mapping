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
from typing import Dict, List, Optional, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer, util

from nlp.clinical_rules import (
    ABBREVIATIONS,
    BRIDGE_TERMS,
    CHAPTER_GATES,
    DATA_FIXES,
    DIRECT_ALIASES,
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

_NOISE_RE = [re.compile(p) for p in NOISE_PREFIXES]
_NOISE_SUFFIX_RE = [re.compile(p) for p in NOISE_SUFFIXES]
_ABBR_RE = [(re.compile(p), r) for p, r in ABBREVIATIONS.items()]
_QUERY_ABBR_RE = [(re.compile(p), r) for p, r in QUERY_ABBREVIATIONS.items()]
_MAX_NER_NGRAM = 12


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

    def _cache_key(self) -> Tuple[str, str]:
        """
        Sinh khóa cache từ (định danh mô hình, nội dung danh mục).

        Dùng tên thư mục thay cho đường dẫn tuyệt đối để cache còn dùng được khi
        chép dự án sang máy khác, và dùng hash NỘI DUNG thay cho số lượng bản ghi
        để không bao giờ nạp nhầm cache cũ khi danh mục đổi nội dung mà giữ
        nguyên số dòng.
        """
        if os.path.isdir(self.model_name):
            model_key = os.path.basename(os.path.normpath(self.model_name))
        else:
            model_key = self.model_name.replace("/", "_").replace("\\", "_").replace(":", "_")

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
        self._ascii_to_norm: Dict[str, str] = {}
        _restore_priority: Dict[str, int] = {}
        _TYPE_RANK = {"clinical_alias": 3, "official_vi": 2, "synonym": 1, "official_en": 0}

        for entry in self.reference_entries:
            norm = self.normalize_text(entry["text"])
            self._entry_norm.append(norm)
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
    def _calibrate(relative: float, similarity: float) -> float:
        """Quy đổi (xác suất tương đối, độ tương đồng thô) sang độ tin cậy %."""
        absolute = (similarity - SIM_FLOOR) / (SIM_CEIL - SIM_FLOOR)
        absolute = max(0.0, min(1.0, absolute))
        blended = W_RELATIVE * relative + W_ABSOLUTE * absolute
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
    def query(self, user_query: str, top_k: int = 4) -> List[dict]:
        """Trả về danh sách mã ICD-10 ứng viên đã tái xếp hạng và hiệu chuẩn."""
        expanded = self.expand_query(user_query)
        if not expanded:
            return []

        query_embedding = self.model.encode(expanded, convert_to_tensor=True)
        cos_scores = util.cos_sim(query_embedding, self.reference_embeddings)[0].cpu().numpy()

        q_labels = self._polarity_labels(expanded)
        q_tokens = frozenset(self._content_tokens(expanded))
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
            if entry_norm and len(entry_norm) >= 5 and entry_norm in expanded:
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

            current = best.get(code)
            if current is None or score > current["score"]:
                best[code] = {
                    "score": score,
                    "raw": float(cos_scores[idx]),
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
            confidence = self._calibrate(relative, info["raw"])
            results.append({
                "code": sanitize_icd10_code(code),
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
