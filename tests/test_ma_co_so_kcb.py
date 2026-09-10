# -*- coding: utf-8 -*-
"""
Mã cơ sở khám chữa bệnh phải đúng dạng thì Gateway mới được chạy.

Mã CSKCB do cơ quan BHXH cấp là khóa mà cổng tiếp nhận giám định BHYT và XML
theo QĐ 130 đều dùng. Trong mã nguồn này nó còn nằm trong `stable_condition_key`,
trong `meta.tag` và trong `mrn_system` - tức ba chỗ định danh của MỌI bản ghi mà
bản Gateway này ghi lên trục.

Vì vậy mã sai dạng không phải một lỗi nhập liệu, mà là một lỗi cấu hình làm hỏng
toàn bộ dữ liệu đầu ra. Nó phải lộ ra lúc bật máy - lúc còn sửa được bằng cách
đổi biến môi trường - chứ không phải sau vài trăm ca khám, khi sửa nghĩa là phải
migrate lại tất cả.
"""
import pytest

from backend.fhir_helper import validate_facility_code


# --- Nhận mã đúng dạng -----------------------------------------------------

@pytest.mark.parametrize("ma", [
    "01001",   # Hà Nội
    "79135",   # TP Hồ Chí Minh
    "24012",   # mã tỉnh khác, để chốt là không ràng buộc riêng một tỉnh nào
])
def test_ma_cskcb_dung_dang_duoc_nhan(ma):
    assert validate_facility_code(ma) == ma


def test_khoang_trang_thua_duoc_bo():
    """
    Một khoảng trắng thừa trong biến môi trường là đủ để `to_fhir_id` sinh ra
    một system khác, tức Gateway tự nhận mình là một bệnh viện khác mà không ai
    thấy. Chuẩn hóa ở cửa vào rẻ hơn nhiều so với đi tìm nguyên nhân sau này.
    """
    assert validate_facility_code("  01001  ") == "01001"


# --- Chặn mã sai dạng ------------------------------------------------------

@pytest.mark.parametrize("sai", [
    "BV-DEMO-01",   # mã tự đặt của môi trường trình diễn
    "0100",         # thiếu một ký tự
    "010011",       # thừa một ký tự
    "A1001",        # hai ký tự đầu không phải mã tỉnh
    "01A01",        # mã CSKCB toàn chữ số, không có chữ cái
    "Bệnh viện A",  # tên cơ sở gõ nhầm vào ô mã
])
def test_ma_sai_dang_bi_tu_choi(sai):
    with pytest.raises(ValueError) as loi:
        validate_facility_code(sai)
    assert "SMIG_ALLOW_DEMO_FACILITY" in str(loi.value)


@pytest.mark.parametrize("rong", [None, "", "   "])
def test_bo_trong_bi_tu_choi(rong):
    """
    Khác định danh bệnh nhân: bỏ trống CCCD là hợp lệ vì có phương án lùi, còn
    bỏ trống mã cơ sở thì `mrn_system` lùi về nhãn "khong-ro-co-so" và mọi bệnh
    viện chạy Gateway này đều dùng chung một nhãn đó.
    """
    with pytest.raises(ValueError) as loi:
        validate_facility_code(rong)
    assert "SMIG_FACILITY_CODE" in str(loi.value)


# --- Cờ demo ---------------------------------------------------------------

def test_co_demo_cho_qua_ma_tu_dat():
    assert validate_facility_code("BV-DEMO-01", allow_demo=True) == "BV-DEMO-01"


def test_co_demo_van_khong_cho_qua_ma_rong():
    """Nới lỏng cho mã tự đặt là một chuyện, chấp nhận KHÔNG có mã là chuyện khác."""
    with pytest.raises(ValueError):
        validate_facility_code("", allow_demo=True)


# --- Chốt lúc khởi động ----------------------------------------------------

def test_ma_sai_dang_lam_gateway_dung_khi_khoi_dong(gateway, monkeypatch):
    """Tiêu chí xong của T1.1(b): sai cấu hình thì chết lúc bật máy."""
    monkeypatch.setattr(gateway, "FACILITY_CODE", "BV-DEMO-01")
    monkeypatch.setattr(gateway, "ALLOW_DEMO_FACILITY", False)

    with pytest.raises(RuntimeError) as loi:
        gateway._chot_ma_co_so()
    assert "SMIG_FACILITY_CODE" in str(loi.value)


def test_ma_dung_dang_duoc_chuan_hoa_vao_cau_hinh(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "FACILITY_CODE", "  01001  ")
    monkeypatch.setattr(gateway, "ALLOW_DEMO_FACILITY", False)

    gateway._chot_ma_co_so()
    assert gateway.FACILITY_CODE == "01001"
