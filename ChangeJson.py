# -*- coding: utf-8 -*-
"""
Dựng danh mục ICD-10 cho NLPEngine từ file Excel của Bộ Y tế.

Cách chạy:
    .venv\\Scripts\\python ChangeJson.py --dry-run    # chỉ báo cáo, không ghi
    .venv\\Scripts\\python ChangeJson.py              # ghi thật

Bản trước chỉ đọc sheet đầu tiên ('ICD10', 12.219 dòng) trong khi file có 14
sheet. Riêng sheet 'E - ICD10 Mã bệnh chính' chứa 13.026 mã, trong đó hơn bốn
nghìn mã - phần lớn là nhánh mở rộng năm ký tự đặc thù Việt Nam (B37.00, C02.10)
- không hề có trong danh mục sinh ra. Đây đúng là nhóm mã Thông tư 06/2026/TT-BYT
quy định phải mã hóa được, nên thiếu là thiếu ở chỗ không được phép thiếu.

Các sheet phụ lục còn mang ràng buộc lâm sàng mà bản trước bỏ trắng: A2 đánh dấu
mã không được dùng làm bệnh chính, A3.x giới hạn tuổi, A4.x giới hạn giới tính,
A1 ghép cặp dagger/asterisk. Những trường này cho phép Gateway loại sớm ứng viên
sai về mặt lâm sàng thay vì chỉ xếp hạng theo độ tương đồng văn bản.

CẢNH BÁO: ghi thật sẽ đổi khóa cache embedding, lần khởi động kế tiếp hệ thống
phải mã hóa lại toàn bộ danh mục (15-40 phút trên CPU, lâu hơn trước vì danh mục
lớn hơn). Chạy khi không cần demo.
"""

import argparse # import argparse is used for parsing command-line arguments in Python scripts. It allows you to define what arguments your program requires, handle optional arguments, and automatically generate help and usage messages. In this script, it is used to provide a "--dry-run" option that allows the user to run the script without making any changes to the output file.
import json
import os
import re
import sys
import unicodedata
from collections import OrderedDict

import pandas as pd

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

EXCEL_PATH = "dataICD10.xlsx"
# Ghi thẳng vào nơi NLPEngine đọc. Bản trước ghi ra backend/data/icd10_db.json
# trong khi engine lại đọc nlp/data/icd10_db.json, nên chạy script không có tác dụng.
OUTPUT_PATH = os.path.join("nlp", "data", "icd10_db.json")

SHEET_MASTER = "ICD10"

# Sheet bổ sung mã bệnh. Cùng bố cục: cột 'Mã' ở dạng liền, kèm tên Việt/Anh.
SHEETS_CODES = [
    "E - ICD10 Mã bệnh chính",
    "E1 - Không ghép DRG",
    "A2 Mã ICD10 ko mã bệnh chính",
]

# Các sheet phụ lục A3. Nhãn khoảng tuổi KHÔNG lấy ở đây mà đọc từ chính dòng
# tiêu đề "Phụ lục ..." bên trong sheet - xem `doc_rang_buoc_tuoi`. Giá trị trong
# dict chỉ còn là nhãn dự phòng để tra cứu khi đọc hỏng.
SHEETS_AGE = {
    "A3.1": "0-365 ngày",
    "A3.2-A3.3-A3.4": "0 ngày - 2 tuổi",
    "A3.5": "trên 27 ngày tuổi",
    "A3.6": "từ 1 tuổi",
    "A3.7 - A3.8": "8-19 tuổi",
    "A3.9": "trên 15 tuổi",
    "A3.10": "trên 30 tuổi",
}

# Sheet ràng buộc giới tính -> nhãn gắn vào meta.sex_constraint
SHEETS_SEX = {
    "A4.1": "female",
    "A4.2": "male",
}

SHEET_DAGGER = "A1"
SHEET_NO_PRIMARY = "A2 Mã ICD10 ko mã bệnh chính"

# Danh mục vá thủ công. Sheet 'E' liệt kê 4.242 mã mà cột tên bệnh bỏ trắng ở cả
# hai file Excel nguồn - trong đó 4.066 mã thuộc nhánh mở rộng năm ký tự. Mã
# không có tên thì không embedding được, nên không thể nạp thẳng vào danh mục.
# Cũng không được mượn tên của mã cha: B37.00 đến B37.09 sẽ cùng mang tên "Viêm
# miệng do candida", vừa không phân biệt được với nhau vừa cạnh tranh với chính
# B37.0 hợp lệ, kéo độ chính xác xuống thay vì lên.
#
# File này là chỗ bổ sung những mã đã tra được tên từ nguồn chính thức. Mỗi mục
# bắt buộc ghi `source` để về sau còn kiểm chứng được lấy tên từ đâu.
#
# Hiện đang để rỗng, có lý do. Mã 3 ký tự duy nhất thiếu tên là A91, và thoạt
# nhìn thì đáng bổ sung "Sốt xuất huyết Dengue". Nhưng danh mục đã có sẵn A97
# mang đúng tên đó, kèm A97.0/A97.1/A97.2 theo phân loại mức độ mới của WHO -
# tức Việt Nam dùng A97 cho mặt bệnh này, còn A91 chỉ là vết tích trong phụ lục.
# Thêm A91 vào sẽ tạo hai mã trùng tên hoàn toàn, xẻ đôi điểm khớp và làm hỏng
# cả hai. Tra được tên thật của mã nào thì thêm mã đó, đừng suy đoán.
SUPPLEMENT_PATH = os.path.join("nlp", "data", "icd10_supplement.json")


def nfc(text: str) -> str:
    """
    Dựng ký tự tổ hợp thành ký tự dựng sẵn.

    File Excel nguồn trộn hai cách mã hóa: phần lớn ở dạng dựng sẵn (NFC) nhưng
    197 dòng ở dạng tổ hợp (NFD), ví dụ "không" được lưu là k h o U+0302 n g.
    Dấu tổ hợp không thuộc lớp \\w nên bộ chuẩn hóa của NLPEngine thay nó bằng
    dấu cách và cắt "không" thành "kho ng" - mã A99 vì thế tụt từ hạng 1 xuống
    hạng 3. Chuẩn hóa ngay từ khâu nhập liệu để lỗi không quay lại.
    """
    return unicodedata.normalize("NFC", str(text))


def strip_diacritics(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def clean_code(raw) -> str:
    """
    Gỡ ký hiệu dao găm/sao và khoảng trắng khỏi mã.

    Danh mục dùng † cho hệ thống dagger/asterisk của WHO. Ký tự này không thuộc
    mã ICD-10 và sẽ bị máy chủ FHIR từ chối, nên phải loại ngay từ khâu nhập liệu.
    """
    return re.sub(r"[†*‡+\s]", "", str(raw)).strip().upper()


def to_dotted(code: str) -> str:
    """
    Đưa mã về dạng chuẩn có dấu chấm - dạng dùng để liên thông FHIR.

    Sheet phụ lục lưu mã ở dạng liền ('A066', 'B3700') còn sheet danh mục chính
    lưu dạng có chấm ('A06.6'). Ba ký tự đầu là danh mục, phần còn lại là danh
    mục con: A066 -> A06.6, B3700 -> B37.00. Mã ba ký tự giữ nguyên.
    """
    code = clean_code(code)
    if "." in code:
        return code
    if len(code) <= 3:
        return code
    return f"{code[:3]}.{code[3:]}"


def is_valid_code(code: str) -> bool:
    """Chấp nhận cả nhánh mở rộng năm ký tự của Việt Nam (I10.00)."""
    return bool(re.fullmatch(r"[A-Z]\d{2}(\.\d{1,2})?", code))


# Cặp thuật ngữ hành chính <-> thuật ngữ bác sĩ dùng khi viết chẩn đoán.
#
# Danh mục Bộ Y tế đặt tên khối u ác theo lối văn bản: 426 mã mang cụm "u ác",
# 40 mã "u ác tính". Trong khi đó chỉ 81 mã có chữ "ung thư", và chúng dồn vào
# vài nhóm hẹp - C22 (gan), C46 (Kaposi), D00 (tại chỗ).
#
# Hậu quả đo được trên tập kiểm tra độc lập: "ung thư phổi", "ung thư dạ dày",
# "ung thư đại tràng" đều trả về C22.0 "Ung thư biểu mô tế bào gan", vì đó là
# một trong số ít tên có sẵn chữ "ung thư" để bám vào. Ba mã đúng C34, C16, C18
# lại mang tên "U ác của..." nên không khớp được từ nào.
#
# Sinh thêm biến thể để bác sĩ gõ theo lối nào cũng tra ra. Thứ tự trong danh
# sách có ý nghĩa: cụm dài phải đứng trước cụm ngắn, nếu không "u ác tính" sẽ bị
# luật "u ác" cắt trước thành "ung thư tính".
CLINICAL_TERM_VARIANTS = [
    ("u ác tính", "ung thư"),
    ("u ác", "ung thư"),
]


def build_term_variants(lowered: str) -> list:
    """
    Sinh biến thể theo cặp thuật ngữ hành chính <-> thông dụng.

    Dừng ngay sau cụm khớp đầu tiên. Nếu để chạy hết danh sách thì "u ác tính
    của da" vừa sinh "ung thư của da" (đúng) vừa sinh "ung thư tính của da"
    (rác), do luật "u ác" cũng khớp trên chính chuỗi gốc đó.
    """
    for formal, common in CLINICAL_TERM_VARIANTS:
        if formal in lowered:
            return [lowered.replace(formal, common)]
    return []


def build_synonyms(name_vi: str, code_no_dot: str, code: str) -> list:
    """
    Sinh biến thể tìm kiếm thật sự có ích.

    Bản trước chỉ tạo `[name_vi.lower()]` - tức bản sao viết thường của chính tên
    bệnh, không thêm một chút thông tin nào nhưng lại nhân ba số vector phải mã hóa.
    Ở đây sinh biến thể không dấu (bác sĩ hay gõ nhanh không dấu) và mã rút gọn.
    """
    synonyms = []
    lowered = name_vi.lower().strip()

    no_diacritics = strip_diacritics(lowered)
    if no_diacritics != lowered:
        synonyms.append(no_diacritics)

    if code_no_dot and code_no_dot.lower() not in (code.lower(), ""):
        synonyms.append(code_no_dot.lower())

    # Bỏ phần chú thích trong ngoặc: "Bệnh X (Chưa có biến chứng)" -> "bệnh x"
    without_paren = re.sub(r"\s*\([^)]*\)\s*", " ", lowered).strip()
    if without_paren and without_paren != lowered:
        synonyms.append(without_paren)

    # Biến thể theo cách bác sĩ quen viết ("u ác của phổi" -> "ung thư của phổi"),
    # kèm cả dạng không dấu của biến thể đó.
    for variant in build_term_variants(lowered):
        synonyms.append(variant)
        variant_no_diacritics = strip_diacritics(variant)
        if variant_no_diacritics != variant:
            synonyms.append(variant_no_diacritics)

    return list(dict.fromkeys(s for s in synonyms if s))


def add_alias(entry: dict, name: str) -> bool:
    """
    Nhập tên bệnh của một dòng trùng mã vào synonyms thay vì vứt bỏ.

    Hai dòng cùng mã sau khi gỡ † thường là cặp dagger/asterisk - bệnh nguyên và
    biểu hiện - mang tên khác nhau. Bản trước `continue` thẳng, mất luôn từ khóa
    tìm kiếm của dòng sau. Giữ lại làm biến thể thì tra cứu theo tên nào cũng ra.
    """
    name = nfc(name).strip()
    if not name:
        return False
    lowered = name.lower()
    if lowered == entry["name_vi"].lower() or lowered in entry["synonyms"]:
        return False
    entry["synonyms"].append(lowered)
    no_diacritics = strip_diacritics(lowered)
    if no_diacritics != lowered and no_diacritics not in entry["synonyms"]:
        entry["synonyms"].append(no_diacritics)
    return True


def find_code_column(frame: pd.DataFrame):
    """Chọn cột chứa nhiều mã ICD hợp lệ nhất - bố cục các sheet phụ lục không đồng nhất."""
    best, best_hits = None, 0
    for column in frame.columns:
        hits = sum(1 for v in frame[column].dropna()
                   if is_valid_code(to_dotted(v)))
        if hits > best_hits:
            best, best_hits = column, hits
    return best, best_hits


def read_sheet(sheet: str):
    """Đọc sheet với dòng tiêu đề dò tự động - phụ lục dùng header ở dòng khác nhau."""
    for header in (2, 1, 0, 3):
        try:
            frame = pd.read_excel(EXCEL_PATH, sheet_name=sheet, header=header)
        except Exception:
            continue
        column, hits = find_code_column(frame)
        if column is not None and hits > 0:
            return frame, column
    return None, None


def load_master(stats: dict) -> "OrderedDict[str, dict]":
    """Danh mục gốc - sheet duy nhất có đủ thông tin chương/nhóm."""
    frame = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_MASTER, header=2)
    entries = OrderedDict()
    merged = 0

    for _, row in frame.iterrows():
        if pd.isna(row["MÃ BỆNH"]) or pd.isna(row["TÊN BỆNH"]):
            continue
        code = to_dotted(row["MÃ BỆNH"])
        if not is_valid_code(code):
            stats["master_invalid"] += 1
            continue

        name_vi = nfc(row["TÊN BỆNH"]).strip()
        if code in entries:
            if add_alias(entries[code], name_vi):
                merged += 1
            continue

        code_no_dot = clean_code(row["MÃ BỆNH KHÔNG DẤU"]) if pd.notna(row["MÃ BỆNH KHÔNG DẤU"]) else ""
        # Cột nguồn để trống ở một số dòng, mà dạng không dấu chỉ là mã đã gỡ dấu
        # chấm nên suy ra được: giữ trường này luôn có giá trị để phía dùng khỏi
        # phải tự xử lý trường hợp rỗng.
        code_no_dot = code_no_dot or code.replace(".", "")
        name_en = nfc(row["DISEASE NAME"]).strip() if pd.notna(row["DISEASE NAME"]) else ""

        entries[code] = {
            "code": code,
            # Dạng liền không dấu chấm (A00.0 -> A000) cho HIS/báo cáo dùng mã
            # rút gọn. Mã chuẩn để liên thông FHIR vẫn là `code`.
            "code_no_dot": code_no_dot,
            "name_vi": name_vi,
            "name_en": name_en,
            "synonyms": build_synonyms(name_vi, code_no_dot, code),
            "meta": {
                "chapter_no": nfc(row["STT CHƯƠNG"]).strip() if pd.notna(row["STT CHƯƠNG"]) else "",
                "chapter_name": nfc(row["TÊN CHƯƠNG"]).strip() if pd.notna(row["TÊN CHƯƠNG"]) else "",
                "group_code": nfc(row["MÃ NHÓM PHỤ 1"]).strip() if pd.notna(row["MÃ NHÓM PHỤ 1"]) else "",
                "type_name": nfc(row["TÊN LOẠI"]).strip() if pd.notna(row["TÊN LOẠI"]) else "",
                "source": SHEET_MASTER,
            },
        }

    stats["master_rows"] = len(frame)
    stats["master_codes"] = len(entries)
    stats["master_merged"] = merged
    return entries


def inherit_meta(code: str, entries: "OrderedDict[str, dict]") -> dict:
    """
    Mượn thông tin chương/nhóm từ mã cha ba ký tự.

    Sheet phụ lục chỉ có mã và tên bệnh, không có cột chương. Mã con luôn cùng
    chương với mã cha (B37.00 thuộc cùng chương với B37) nên suy ra được, khỏi
    để trống làm hỏng phần lọc theo chương của NLPEngine.
    """
    parent = entries.get(code[:3])
    if parent:
        meta = dict(parent["meta"])
        meta["type_name"] = parent["name_vi"]
        return meta
    return {"chapter_no": "", "chapter_name": "", "group_code": "", "type_name": ""}


def merge_code_sheets(entries: "OrderedDict[str, dict]", stats: dict) -> None:
    """Bổ sung mã từ các sheet phụ lục - đây là nơi 4.000+ mã bị bỏ sót được nạp vào."""
    for sheet in SHEETS_CODES:
        frame, column = read_sheet(sheet)
        if frame is None:
            stats["sheet_report"].append((sheet, 0, 0, 0, "KHÔNG ĐỌC ĐƯỢC"))
            continue

        name_col = next((c for c in frame.columns if "tên bệnh" in str(c).lower()
                         and "anh" not in str(c).lower()), None)
        en_col = next((c for c in frame.columns if "anh" in str(c).lower()
                       or "description" in str(c).lower()), None)

        added = aliased = seen = noname = 0
        for _, row in frame.iterrows():
            code = to_dotted(row[column]) if pd.notna(row[column]) else ""
            if not is_valid_code(code):
                continue
            seen += 1
            name_vi = nfc(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""
            if not name_vi:
                # Mã có trong phụ lục nhưng cột tên bỏ trắng. Bỏ qua thay vì mượn
                # tên mã cha - xem ghi chú ở SUPPLEMENT_PATH.
                if code not in entries:
                    noname += 1
                    stats["missing_names"].append(code)
                continue

            if code in entries:
                if add_alias(entries[code], name_vi):
                    aliased += 1
                continue

            code_no_dot = code.replace(".", "")
            name_en = nfc(row[en_col]).strip() if en_col and pd.notna(row[en_col]) else ""
            meta = inherit_meta(code, entries)
            meta["source"] = sheet
            entries[code] = {
                "code": code,
                "code_no_dot": code_no_dot,
                "name_vi": name_vi,
                "name_en": name_en,
                "synonyms": build_synonyms(name_vi, code_no_dot, code),
                "meta": meta,
            }
            added += 1

        stats["sheet_report"].append((sheet, seen, added, aliased, noname))


# --- Rang buoc tuoi: doc nhan tu chinh dong tieu de phu luc -----------------

# Mot sheet co the chua NHIEU phu luc voi khoang tuoi KHAC HAN nhau:
#
#   'A3.7 - A3.8'      A3.7 Benh cua tuoi day thi      8 - 19 tuoi   (3 ma)
#                      A3.8 Benh san phu khoa          9 - 60 tuoi   (518 ma)
#   'A3.2-A3.3-A3.4'   A3.2 Benh tre nho               0 ngay - 2 tuoi
#                      A3.3 Benh o tre lon             0 ngay - 10 tuoi
#                      A3.4 Benh o thanh thieu nien    0 ngay - 19 tuoi
#
# Ban truoc gan MOT nhan cho ca sheet, lay theo phu luc dau tien. Hau qua: 518 ma
# san phu khoa mang khoang "8-19 tuoi". Neu dem rang buoc nay ra loc ung vien thi
# san phu 30 tuoi bi loai het ma chuong O - dung kieu hong ma rang buoc lam sang
# sinh ra de chan, chi khac la no chan nham nguoi.
#
# Nay doc thang khoang tuoi trong dong "Phu luc ..." va ap cho dung nhung ma nam
# duoi dong do. Them phu luc moi cung khong phai sua bang tay nua.
RE_PHU_LUC = re.compile(r"Phụ\s*lục\s*(A3\.\d+)", re.IGNORECASE)
RE_KHOANG_TUOI = re.compile(
    r"(?:tuổi\s*hợp\s*lệ|phù\s*hợp\s*tuổi)\s*[:：]?\s*([^)]+)", re.IGNORECASE)


def chuan_hoa_nhan_tuoi(raw: str) -> str:
    """Gọn lại chuỗi khoảng tuổi đọc từ tiêu đề, giữ nguyên nghĩa."""
    nhan = re.sub(r"\s+", " ", (raw or "")).strip(" .:-–—)")
    # Excel dùng cả gạch ngang thường lẫn gạch dài; thống nhất một dạng để giá trị
    # so sánh được và bộ phân tích chỉ phải xử một ký tự.
    return nhan.replace("–", "-").replace("—", "-").strip()


def doc_rang_buoc_tuoi(sheet: str):
    """
    Đọc một sheet phụ lục A3 thành ``[(mã, nhãn tuổi)]``.

    Trả danh sách rỗng nếu không đọc được sheet - phía gọi ghi vào báo cáo chứ
    không dừng cả lần dựng danh mục vì một phụ lục.
    """
    try:
        frame = pd.read_excel(EXCEL_PATH, sheet_name=sheet, header=None)
    except Exception:
        return []

    ket_qua = []
    nhan_hien_tai = None
    for _, row in frame.iterrows():
        o_dau = row.iloc[0]
        if pd.isna(o_dau):
            continue
        text = nfc(str(o_dau)).strip()

        if RE_PHU_LUC.search(text):
            khoang = RE_KHOANG_TUOI.search(text)
            # Tiêu đề không nêu khoảng tuổi thì bỏ nhãn cũ đi thay vì dùng tiếp:
            # gán nhầm khoảng của phụ lục trước chính là lỗi đang sửa.
            nhan_hien_tai = chuan_hoa_nhan_tuoi(khoang.group(1)) if khoang else None
            continue

        if nhan_hien_tai and is_valid_code(to_dotted(text)):
            ket_qua.append((to_dotted(text), nhan_hien_tai))

    return ket_qua


def apply_constraints(entries: "OrderedDict[str, dict]", stats: dict) -> None:
    """
    Gắn ràng buộc lâm sàng vào từng mã.

    Phụ lục A2/A3/A4 quy định mã nào không được làm bệnh chính, mã nào chỉ hợp lệ
    với một khoảng tuổi hoặc một giới tính. Gateway dùng những trường này để loại
    ứng viên sai ngay ở khâu xếp hạng - ví dụ không đề xuất mã sản khoa cho bệnh
    nhân nam - thay vì chỉ dựa vào độ tương đồng văn bản.
    """
    def mark(sheet: str, field: str, value):
        frame, column = read_sheet(sheet)
        if frame is None:
            stats["constraint_report"].append((sheet, field, 0, "KHÔNG ĐỌC ĐƯỢC"))
            return
        hits = 0
        for raw in frame[column].dropna():
            code = to_dotted(raw)
            if code in entries:
                entries[code]["meta"][field] = value
                hits += 1
        stats["constraint_report"].append((sheet, f"{field}={value}", hits, "ok"))

    for sheet in SHEETS_AGE:
        cap = doc_rang_buoc_tuoi(sheet)
        if not cap:
            stats["constraint_report"].append((sheet, "age_constraint", 0, "KHÔNG ĐỌC ĐƯỢC"))
            continue
        theo_nhan = {}
        hits = 0
        for code, nhan in cap:
            if code in entries:
                entries[code]["meta"]["age_constraint"] = nhan
                theo_nhan[nhan] = theo_nhan.get(nhan, 0) + 1
                hits += 1
        mo_ta = ", ".join(f"{n}={c}" for n, c in sorted(theo_nhan.items()))
        stats["constraint_report"].append((sheet, f"age_constraint [{mo_ta}]", hits, "ok"))
    for sheet, label in SHEETS_SEX.items():
        mark(sheet, "sex_constraint", label)

    frame, column = read_sheet(SHEET_NO_PRIMARY)
    if frame is not None:
        hits = 0
        for raw in frame[column].dropna():
            code = to_dotted(raw)
            if code in entries:
                entries[code]["meta"]["can_be_primary"] = False
                hits += 1
        stats["constraint_report"].append((SHEET_NO_PRIMARY, "can_be_primary=False", hits, "ok"))

    # A1 ghép cặp bệnh nguyên (†) với biểu hiện (*). Giữ quan hệ này để phía sau
    # có thể gợi ý mã còn lại của cặp khi bác sĩ mới chọn một mã.
    try:
        frame = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_DAGGER, header=2)
        pairs = 0
        for _, row in frame.iterrows():
            dagger = to_dotted(row["Dagger (†)"]) if pd.notna(row.get("Dagger (†)")) else ""
            aster = to_dotted(row["Asterix (*)"]) if pd.notna(row.get("Asterix (*)")) else ""
            if dagger in entries and is_valid_code(aster):
                entries[dagger]["meta"].setdefault("asterisk_codes", [])
                if aster not in entries[dagger]["meta"]["asterisk_codes"]:
                    entries[dagger]["meta"]["asterisk_codes"].append(aster)
                    pairs += 1
        stats["constraint_report"].append((SHEET_DAGGER, "asterisk_codes", pairs, "ok"))
    except Exception as exc:
        stats["constraint_report"].append((SHEET_DAGGER, "asterisk_codes", 0, f"lỗi: {exc}"))


def merge_supplement(entries: "OrderedDict[str, dict]", stats: dict) -> None:
    """
    Nạp danh mục vá thủ công - nguồn duy nhất được phép thêm mã ngoài file Excel.

    Giữ riêng khỏi luồng đọc Excel để mọi mã thêm bằng tay đều có vết: chạy lại
    script không làm mất, và người đọc báo cáo tra được tên bệnh lấy từ văn bản nào.
    """
    if not os.path.exists(SUPPLEMENT_PATH):
        stats["supplement"] = (0, 0, "không có file")
        return

    with open(SUPPLEMENT_PATH, encoding="utf-8") as f:
        items = json.load(f)

    added = aliased = 0
    for item in items:
        code = to_dotted(item.get("code", ""))
        name_vi = nfc(item.get("name_vi", "")).strip()
        if not is_valid_code(code) or not name_vi:
            continue

        if code in entries:
            if add_alias(entries[code], name_vi):
                aliased += 1
            continue

        code_no_dot = code.replace(".", "")
        meta = inherit_meta(code, entries)
        meta["source"] = item.get("source", "bổ sung thủ công")
        entries[code] = {
            "code": code,
            "code_no_dot": code_no_dot,
            "name_vi": name_vi,
            "name_en": nfc(item.get("name_en", "")).strip(),
            "synonyms": build_synonyms(name_vi, code_no_dot, code),
            "meta": meta,
        }
        added += 1

    stats["supplement"] = (added, aliased, "ok")


def report(entries: "OrderedDict[str, dict]", stats: dict, old_count: int) -> None:
    print("\n" + "=" * 68)
    print("SHEET DANH MỤC GỐC")
    print("=" * 68)
    print(f"  {SHEET_MASTER}: {stats['master_rows']} dòng -> {stats['master_codes']} mã "
          f"({stats['master_merged']} tên bệnh trùng mã được giữ làm biến thể)")

    print("\n" + "=" * 68)
    print("SHEET BỔ SUNG MÃ")
    print("=" * 68)
    print(f"  {'Sheet':<32}{'Mã đọc':>8}{'Thêm mới':>10}{'Biến thể':>10}{'Trắng tên':>11}")
    for sheet, seen, added, aliased, noname in stats["sheet_report"]:
        print(f"  {sheet:<32}{seen:>8}{added:>10}{aliased:>10}{noname:>11}")

    unique_missing = sorted(set(stats["missing_names"]))
    if unique_missing:
        print(f"\n  {len(unique_missing)} mã có trong phụ lục nhưng nguồn bỏ trắng tên bệnh")
        print("  -> không nạp được (không có gì để so khớp ngữ nghĩa).")
        print(f"  -> tra tên từ QĐ 4469/QĐ-BYT rồi bổ sung vào {SUPPLEMENT_PATH}")
        by_depth = {}
        for code in unique_missing:
            by_depth[len(code.replace(".", ""))] = by_depth.get(len(code.replace(".", "")), 0) + 1
        for depth in sorted(by_depth):
            print(f"       {depth} ký tự: {by_depth[depth]:>5}")

    added_sup, aliased_sup, status_sup = stats.get("supplement", (0, 0, "-"))
    print(f"\n  Danh mục vá thủ công: thêm {added_sup}, biến thể {aliased_sup} ({status_sup})")

    print("\n" + "=" * 68)
    print("RÀNG BUỘC LÂM SÀNG")
    print("=" * 68)
    for sheet, field, hits, status in stats["constraint_report"]:
        print(f"  {sheet:<32}{field:<26}{hits:>6}  {status}")

    depth = {3: 0, 4: 0, 5: 0}
    for code in entries:
        depth[len(code.replace(".", ""))] = depth.get(len(code.replace(".", "")), 0) + 1
    total_syn = sum(len(e["synonyms"]) for e in entries.values())
    constrained = sum(1 for e in entries.values()
                      if e["meta"].get("age_constraint") or e["meta"].get("sex_constraint"))

    print("\n" + "=" * 68)
    print("KẾT QUẢ")
    print("=" * 68)
    print(f"  Danh mục cũ            : {old_count:>6} mã")
    print(f"  Danh mục mới           : {len(entries):>6} mã   (+{len(entries) - old_count})")
    print(f"    mã 3 ký tự  (I10)    : {depth.get(3, 0):>6}")
    print(f"    mã 4 ký tự  (I10.0)  : {depth.get(4, 0):>6}")
    print(f"    mã 5 ký tự  (I10.00) : {depth.get(5, 0):>6}   <- nhánh mở rộng Việt Nam")
    print(f"  Tổng biến thể tìm kiếm : {total_syn:>6}")
    print(f"  Mã có ràng buộc tuổi/giới: {constrained:>4}")
    print(f"  Mã còn ký tự †         : {sum(1 for c in entries if '†' in c):>6}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dựng danh mục ICD-10 cho NLPEngine.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Chỉ báo cáo số liệu, không ghi đè danh mục.")
    args = parser.parse_args()

    old_count = 0
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            old_count = len(json.load(f))

    stats = {"master_rows": 0, "master_codes": 0, "master_merged": 0,
             "master_invalid": 0, "sheet_report": [], "constraint_report": [],
             "missing_names": []}

    print(f"Đang đọc '{EXCEL_PATH}'...")
    entries = load_master(stats)
    merge_code_sheets(entries, stats)
    merge_supplement(entries, stats)
    # Chạy sau cùng để mã nạp từ phụ lục và danh mục vá cũng được gắn ràng buộc.
    apply_constraints(entries, stats)

    result = sorted(entries.values(), key=lambda e: e["code"])
    report(entries, stats, old_count)

    if args.dry_run:
        print("\n[DRY-RUN] Không ghi file. Bỏ cờ --dry-run để ghi thật.")
        return

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nĐã ghi {len(result)} mã -> {OUTPUT_PATH}")
    print("LƯU Ý: danh mục đã thay đổi, lần khởi động tới hệ thống sẽ phải")
    print("mã hóa lại toàn bộ embedding (15-40 phút trên CPU).")


if __name__ == "__main__":
    main()
