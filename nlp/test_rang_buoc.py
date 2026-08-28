# -*- coding: utf-8 -*-
"""
Ràng buộc lâm sàng theo phụ lục A2/A3/A4 của Bộ Y tế.

Bộ này KHÔNG nạp mô hình: nó kiểm phần luật thuần trên dữ liệu danh mục, nên chạy
trong dưới một giây. Chất lượng xếp hạng của mô hình vẫn là việc của
`nlp/test_nlp.py`.

Lý do có bộ test riêng: dữ liệu ràng buộc từng SAI mà không ai biết. Hai phụ lục
nằm chung một sheet Excel, bộ nạp gán một nhãn cho cả sheet, nên 515 mã mang
khoảng tuổi của phụ lục khác - trong đó 505 mã sản phụ khoa (hợp lệ 9-60 tuổi)
bị gán 8-19 tuổi. Đem ràng buộc đó ra lọc ứng viên thì sản phụ 30 tuổi bị loại
hết mã chương O. Bộ test này chốt cả dữ liệu lẫn luật để lỗi kiểu ấy không quay
lại im lặng.

Cách chạy:
    .venv\\Scripts\\python -m pytest nlp/test_rang_buoc.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.clinical_rules import (  # noqa: E402
    AGE_CONFLICT_PENALTY,
    NON_PRIMARY_PENALTY,
    SEX_CONFLICT_PENALTY,
    phan_tich_khoang_tuoi,
    rang_buoc_lam_sang,
    tuoi_theo_ngay,
)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "icd10_db.json")
NAM = 365


@pytest.fixture(scope="module")
def danh_muc():
    with open(DB_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# --- Bộ phân tích khoảng tuổi ---------------------------------------------

@pytest.mark.parametrize("nhan,mong_doi", [
    ("0 - 365 ngày", (0, 365)),
    ("0 ngày - 2 tuổi", (0, 3 * NAM - 1)),
    ("9 - 60 tuổi", (9 * NAM, 61 * NAM - 1)),
    ("8 - 19 tuổi", (8 * NAM, 20 * NAM - 1)),
    ("1 tuổi trở lên", (NAM, None)),
    ("trên 27 ngày tuổi", (28, None)),
    ("trên 15 tuổi", (15 * NAM, None)),
    ("trên 30 tuổi", (30 * NAM, None)),
])
def test_doc_dung_tung_dang_nhan(nhan, mong_doi):
    assert phan_tich_khoang_tuoi(nhan) == mong_doi


def test_doc_duoc_MOI_nhan_co_that_trong_danh_muc(danh_muc):
    """
    Không nhãn nào trong danh mục được rơi vào diện 'không hiểu'.

    Nhãn đọc không ra thì mã đó lặng lẽ mất ràng buộc - đúng kiểu hỏng chỉ lộ ra
    khi có người bệnh nhận nhầm mã.
    """
    nhan = {e["meta"]["age_constraint"] for e in danh_muc if e["meta"].get("age_constraint")}
    assert nhan, "Danh mục không còn ràng buộc tuổi nào - bộ nạp hỏng?"
    khong_doc_duoc = sorted(v for v in nhan if phan_tich_khoang_tuoi(v) is None)
    assert not khong_doc_duoc, f"Nhãn không đọc được: {khong_doc_duoc}"


def test_nhan_la_bi_bo_qua_chu_khong_doan_bua():
    """Đoán bừa một khoảng tuổi nguy hiểm hơn hẳn việc bỏ qua nó."""
    for rac in ("", None, "tùy trường hợp", "người lớn"):
        assert phan_tich_khoang_tuoi(rac) is None


# --- Dữ liệu danh mục ------------------------------------------------------

def test_ma_san_phu_khoa_mang_dung_khoang_9_60(danh_muc):
    """
    Chốt đúng lỗi đã sửa.

    Phụ lục A3.8 'Bệnh sản phụ khoa' hợp lệ 9-60 tuổi, nhưng nằm chung sheet với
    A3.7 'Bệnh của tuổi dậy thì' (8-19 tuổi). Gán nhầm thì sản phụ 30 tuổi bị
    loại hết mã chương O - ràng buộc lâm sàng quay ra chặn nhầm người.
    """
    chuong_o = [e for e in danh_muc
                if e["code"].startswith("O") and e["meta"].get("age_constraint")]
    assert chuong_o, "Không mã chương O nào có ràng buộc tuổi"

    sai = [e["code"] for e in chuong_o if e["meta"]["age_constraint"] != "9 - 60 tuổi"]
    assert not sai, f"Mã chương O mang khoảng tuổi sai: {sai[:10]}"

    thap, cao = phan_tich_khoang_tuoi(chuong_o[0]["meta"]["age_constraint"])
    assert thap <= 30 * NAM <= cao, "Sản phụ 30 tuổi vẫn phải hợp lệ"


def test_ma_so_sinh_khong_hop_le_voi_nguoi_lon(danh_muc):
    so_sinh = next(e for e in danh_muc
                   if e["meta"].get("age_constraint") == "0 - 365 ngày")
    thap, cao = phan_tich_khoang_tuoi(so_sinh["meta"]["age_constraint"])
    assert cao < 60 * NAM


# --- Luật chấm điểm --------------------------------------------------------

def test_gioi_tinh_xung_dot_bi_ha_bac():
    meta = {"sex_constraint": "female"}
    diem, ghi_chu = rang_buoc_lam_sang(meta, sex="male")
    assert diem == -SEX_CONFLICT_PENALTY
    assert "nữ" in ghi_chu[0]


def test_gioi_tinh_khop_thi_khong_phat():
    assert rang_buoc_lam_sang({"sex_constraint": "female"}, sex="female") == (0.0, [])


def test_tuoi_ngoai_khoang_bi_ha_bac():
    meta = {"age_constraint": "0 - 365 ngày"}
    diem, ghi_chu = rang_buoc_lam_sang(meta, age_days=60 * NAM)
    assert diem == -AGE_CONFLICT_PENALTY
    assert "tuổi" in ghi_chu[0]

    assert rang_buoc_lam_sang(meta, age_days=100) == (0.0, [])


def test_ma_khong_duoc_lam_benh_chinh_bi_ha_bac():
    meta = {"can_be_primary": False}
    diem, ghi_chu = rang_buoc_lam_sang(meta)
    assert diem == -NON_PRIMARY_PENALTY
    assert "bệnh chính" in ghi_chu[0]
    # Khi không xét ở vai trò bệnh chính thì không phạt.
    assert rang_buoc_lam_sang(meta, for_primary=False) == (0.0, [])


def test_thieu_thong_tin_benh_nhan_thi_KHONG_phat():
    """
    Bỏ trống giới tính hay ngày sinh là chuyện thường ở khâu tiếp đón.

    Phạt dựa trên thứ mình không biết là bịa, và nó làm mọi đường gọi cũ - vốn
    không gửi bối cảnh bệnh nhân - lặng lẽ đổi kết quả.
    """
    meta = {"sex_constraint": "female", "age_constraint": "0 - 365 ngày"}
    assert rang_buoc_lam_sang(meta) == (0.0, [])
    assert rang_buoc_lam_sang(meta, sex="", age_days=None) == (0.0, [])


def test_vi_pham_chong_nhau_thi_cong_don():
    meta = {"sex_constraint": "female", "age_constraint": "0 - 365 ngày",
            "can_be_primary": False}
    diem, ghi_chu = rang_buoc_lam_sang(meta, sex="male", age_days=40 * NAM)
    assert diem == pytest.approx(
        -(SEX_CONFLICT_PENALTY + AGE_CONFLICT_PENALTY + NON_PRIMARY_PENALTY))
    assert len(ghi_chu) == 3


def test_gioi_tinh_phat_nang_hon_tuoi():
    """
    Giới tính gần như bất biến; khoảng tuổi chỉ là quy tắc kiểm tra.

    Phụ nữ 65 tuổi vẫn mắc bệnh phụ khoa, nên vi phạm tuổi không được nặng ngang
    vi phạm giới - nếu không thì luật quay ra chặn nhầm người bệnh có thật.
    """
    assert SEX_CONFLICT_PENALTY > AGE_CONFLICT_PENALTY


# --- Đổi ngày sinh sang số ngày tuổi ---------------------------------------

def test_tuoi_theo_ngay():
    from datetime import date
    assert tuoi_theo_ngay("2000-01-01", moc=date(2000, 1, 31)) == 30
    # FHIR dateTime có phần giờ - vẫn phải đọc được.
    assert tuoi_theo_ngay("2000-01-01T08:30:00", moc=date(2000, 1, 31)) == 30
    assert tuoi_theo_ngay(None) is None
    assert tuoi_theo_ngay("khong-phai-ngay") is None
    # Ngày sinh ở tương lai là dữ liệu hỏng, không phải tuổi âm.
    assert tuoi_theo_ngay("2030-01-01", moc=date(2000, 1, 1)) is None


# --- Vị trí dấu thanh trên nguyên âm đôi -----------------------------------

from nlp.clinical_rules import thong_nhat_dau_thanh  # noqa: E402


@pytest.mark.parametrize("go,mong_doi", [
    ("thuỳ", "thùy"),
    ("lan toả", "lan tỏa"),
    ("tích luỹ", "tích lũy"),
    ("hoà bình", "hòa bình"),
    ("khoẻ", "khỏe"),
    ("ngoằn ngoèo", "ngoằn ngòeo"),
])
def test_hai_loi_dat_dau_quy_ve_mot(go, mong_doi):
    """
    "thùy" và "thuỳ" đều đúng chính tả nhưng là hai chuỗi Unicode khác nhau.

    20% danh mục dùng lối này, bộ gõ phổ biến dùng lối kia - nên token không khớp
    và cùng một mã tụt tới 20 điểm tin cậy, đủ để rơi khỏi ngưỡng tự động 85%.
    """
    assert thong_nhat_dau_thanh(go) == mong_doi


@pytest.mark.parametrize("nguyen_ven", ["đột quỵ", "quý", "quỳ", "quỹ", "quỷ", "thủ quỹ"])
def test_khong_dung_toi_chu_QU(nguyen_ven):
    """
    Trong "quý", "đột quỵ" thì `u` thuộc digraph `qu`, không phải nguyên âm đôi.

    Đổi bừa sẽ ra "qúy", "đột qụy" - vừa sai chính tả, vừa đẩy các mã đó RA XA
    tầm khớp thay vì kéo lại gần, tức luật quay ra gây đúng loại hỏng nó sinh ra
    để chữa.
    """
    assert thong_nhat_dau_thanh(nguyen_ven) == nguyen_ven


def test_hai_loi_go_cho_cung_mot_chuoi_chuan(danh_muc):
    """
    Chốt trên dữ liệu thật: mọi tên bệnh trong danh mục, khi gõ theo lối kia,
    phải quy về đúng cùng một chuỗi.
    """
    import unicodedata
    cap = [("oà", "òa"), ("uỳ", "ùy"), ("oả", "ỏa"), ("uỹ", "ũy")]
    kiem = 0
    for e in danh_muc:
        ten = unicodedata.normalize("NFC", e["name_vi"]).lower()
        for cu, moi in cap:
            if cu in ten:
                assert thong_nhat_dau_thanh(ten) == thong_nhat_dau_thanh(ten.replace(cu, moi))
                kiem += 1
                break
    assert kiem > 300, f"Chỉ kiểm được {kiem} mã - dữ liệu danh mục có vấn đề?"
