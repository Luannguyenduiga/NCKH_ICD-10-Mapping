# -*- coding: utf-8 -*-
"""
Chuyển danh mục ICD-10 từ file Excel của Bộ Y tế sang JSON cho NLPEngine.

Cách chạy:
    .venv\\Scripts\\python ChangeJson.py

CẢNH BÁO: chạy lại script này sẽ làm thay đổi nội dung danh mục, khiến khóa cache
embedding đổi theo và hệ thống phải mã hóa lại ~47.000 thuật ngữ (15-40 phút trên
CPU). Chỉ chạy khi thực sự cần cập nhật danh mục.
"""

import json
import os
import re
import sys
import unicodedata

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


def strip_diacritics(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def clean_code(raw: str) -> str:
    """
    Gỡ ký hiệu dao găm/sao khỏi mã.

    Danh mục dùng † cho hệ thống dagger/asterisk của WHO. Ký tự này không thuộc
    mã ICD-10 và sẽ bị máy chủ FHIR từ chối, nên phải loại ngay từ khâu nhập liệu.
    """
    return re.sub(r"[†*‡]", "", str(raw)).strip()


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

    # Khử trùng lặp nhưng giữ thứ tự
    return list(dict.fromkeys(s for s in synonyms if s))


def main():
    print(f"Đang đọc file Excel '{EXCEL_PATH}'...")
    df = pd.read_excel(EXCEL_PATH, header=2)
    print("Các cột trong file Excel:", df.columns.tolist())

    icd10_list = []
    seen_codes = set()
    duplicates = []

    print("Đang xử lý dữ liệu...")
    for _, row in df.iterrows():
        if pd.isna(row["MÃ BỆNH"]) or pd.isna(row["TÊN BỆNH"]):
            continue

        code = clean_code(row["MÃ BỆNH"])
        if not code:
            continue

        if code in seen_codes:
            duplicates.append(code)
            continue
        seen_codes.add(code)

        code_no_dot = clean_code(row["MÃ BỆNH KHÔNG DẤU"]) if pd.notna(row["MÃ BỆNH KHÔNG DẤU"]) else ""
        name_vi = str(row["TÊN BỆNH"]).strip()
        name_en = str(row["DISEASE NAME"]).strip() if pd.notna(row["DISEASE NAME"]) else ""

        icd10_list.append({
            "code": code,
            "name_vi": name_vi,
            "name_en": name_en,
            "synonyms": build_synonyms(name_vi, code_no_dot, code),
            "meta": {
                "chapter_no": str(row["STT CHƯƠNG"]).strip() if pd.notna(row["STT CHƯƠNG"]) else "",
                "chapter_name": str(row["TÊN CHƯƠNG"]).strip() if pd.notna(row["TÊN CHƯƠNG"]) else "",
                "group_code": str(row["MÃ NHÓM PHỤ 1"]).strip() if pd.notna(row["MÃ NHÓM PHỤ 1"]) else "",
                "type_name": str(row["TÊN LOẠI"]).strip() if pd.notna(row["TÊN LOẠI"]) else "",
            },
        })

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(icd10_list, f, ensure_ascii=False, indent=2)

    print(f"\nHoàn tất: {len(icd10_list)} mã bệnh -> {OUTPUT_PATH}")
    if duplicates:
        print(f"Đã loại {len(duplicates)} mã trùng lặp: {sorted(set(duplicates))[:10]}")
    print("\nLƯU Ý: danh mục đã thay đổi, lần khởi động tới hệ thống sẽ phải")
    print("mã hóa lại toàn bộ embedding (15-40 phút trên CPU).")


if __name__ == "__main__":
    main()
