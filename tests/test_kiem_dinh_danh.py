# -*- coding: utf-8 -*-
"""
Định danh toàn quốc phải đúng định dạng thì mới được nhận.

Ca thật đã xảy ra trên trục: tên bệnh "tiêu chảy nhiệt đới" bị gõ nhầm vào ô
CCCD. Gateway nhận, coi đó là một danh tính cấp quốc gia và dựng thêm một hồ sơ
bệnh nhân thứ hai cho cùng một người. Từ đó bác sĩ tra bằng số CCCD thật chỉ ra
chẩn đoán của bệnh viện kia, còn chẩn đoán vừa lập thì treo ở hồ sơ mang định
danh rác - không mất, nhưng không ai tìm thấy.

Mã ICD-10 đã được kiểm định dạng từ đầu. Định danh bệnh nhân còn quan trọng hơn:
mã ICD sai thì bác sĩ nhìn ra ngay, định danh sai thì im lặng tách hồ sơ.
"""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

CHUNG = dict(patient_id="BN-1", patient_name="Nguyễn Văn Hùng",
             raw_clinical_note="tiêu chảy nhiệt đới", icd10_code="A04.9",
             icd10_display="Nhiễm khuẩn đường ruột", confidence_score=93.0,
             encounter_date="2026-08-17")


def _sinh(gateway, **them):
    return gateway.convert_to_fhir(gateway.FHIRConvertRequest(**CHUNG, **them))


# --- Chặn giá trị rác ------------------------------------------------------

@pytest.mark.parametrize("rac", [
    "tiêu chảy nhiệt đới",   # ca thật: tên bệnh gõ nhầm ô
    "Nguyễn Văn Hùng",       # họ tên gõ nhầm ô
    "12345",                 # quá ngắn
    "07907501234567",        # quá dài
    "07907500123X",          # lẫn chữ
])
def test_cccd_sai_dinh_dang_bi_tu_choi(gateway, rac):
    with pytest.raises(ValidationError) as loi:
        _sinh(gateway, citizen_id=rac)
    assert "CCCD" in str(loi.value)


@pytest.mark.parametrize("rac", [
    "tiêu chảy",          # tên bệnh gõ nhầm ô
    "4010120152431",      # 13 chữ số: mẫu cũ thiếu hai chữ cái đầu
    "GD40101",            # mẫu cũ bị cắt cụt
    "123456789",          # 9 chữ số: thiếu một số so với mẫu mới
    "12345678901",        # 11 chữ số: thừa một số so với mẫu mới
    "GD401012015243X",    # đủ 15 ký tự nhưng lẫn chữ ở phần số
])
def test_bhyt_sai_dinh_dang_bi_tu_choi(gateway, rac):
    with pytest.raises(ValidationError) as loi:
        _sinh(gateway, insurance_card=rac)
    assert "BHYT" in str(loi.value)


def test_duong_dong_bo_cung_bi_chan(gateway):
    """
    `/api/fhir/sync` nhận Condition dựng sẵn, nên client tự dựng tài nguyên vẫn
    đưa được định danh rác lên trục nếu chỉ kiểm ở `/api/fhir/condition`.
    """
    condition = _sinh(gateway, citizen_id="079075001234")
    condition["subject"]["identifier"]["value"] = "tiêu chảy nhiệt đới"

    with pytest.raises(HTTPException) as loi:
        gateway.sync_fhir_record({"condition": condition})
    assert loi.value.status_code == 422
    assert "CCCD" in loi.value.detail


# --- Chấp nhận giá trị hợp lệ ---------------------------------------------

def test_chi_cccd_12_so_moi_hop_le(gateway):
    """
    CMND 9 chữ số từng được nhận, nay thì không. Theo Luật Căn cước
    26/2023/QH15, CMND chỉ còn giá trị sử dụng tới hết 31/12/2024, nên một số
    CMND gửi lên hôm nay hoặc là gõ nhầm, hoặc là dữ liệu chưa được cập nhật -
    cả hai đều không đáng đứng ra làm khóa đồng nhất bệnh nhân toàn quốc.
    """
    hop_le = _sinh(gateway, citizen_id="079075001234")
    assert hop_le["subject"]["identifier"]["value"] == "079075001234"

    with pytest.raises(ValidationError) as loi:
        _sinh(gateway, citizen_id="079075001")
    assert "CCCD" in str(loi.value)


@pytest.mark.parametrize("the", [
    "8901234567",         # mẫu cấp từ 01/4/2021, trùng mã số BHXH
    "GD4010120152431",    # mẫu cũ 15 ký tự, còn lưu hành tới khi được đổi
])
def test_bhyt_ca_hai_mau_deu_hop_le(gateway, the):
    """
    QĐ 1666/QĐ-BHXH đổi thẻ sang 10 chữ số từ 01/4/2021, nhưng thẻ mẫu cũ không
    hết hiệu lực cùng ngày. Nhận đúng một mẫu là từ chối phần lớn bệnh nhân đến
    khám hôm nay - hoặc ngược lại, khóa hệ thống vào mẫu đang mất dần.
    """
    tai_nguyen = _sinh(gateway, insurance_card=the)
    dinh_danh = tai_nguyen["subject"]["identifier"]
    assert dinh_danh["value"] == the
    assert "bhyt" in dinh_danh["system"]


def test_bhyt_mau_moi_van_dung_lam_khoa_dong_nhat(gateway):
    """
    Thẻ mẫu mới trùng mã số BHXH nên vẫn là định danh toàn quốc: thiếu CCCD thì
    nó phải đứng ra làm khóa, KHÔNG được lùi về mã bệnh án nội viện - lùi là mất
    khả năng đồng nhất bệnh nhân giữa các viện.
    """
    tai_nguyen = _sinh(gateway, citizen_id=None, insurance_card="8901234567")
    assert "mrn" not in tai_nguyen["subject"]["identifier"]["system"]


def test_bo_trong_van_hop_le(gateway):
    """Không phải bệnh nhân nào cũng có giấy tờ lúc tiếp nhận."""
    tai_nguyen = _sinh(gateway, citizen_id=None, insurance_card="")
    # Lùi về mã bệnh án + mã cơ sở, đúng như trước khi có lớp kiểm này.
    assert "mrn" in tai_nguyen["subject"]["identifier"]["system"]


# --- Chuẩn hóa dấu phân cách ----------------------------------------------

def test_dau_cach_va_gach_khong_de_ra_hai_danh_tinh(gateway):
    """
    Cùng một số, gõ khác cách nhau thì phải ra cùng một định danh. Giữ nguyên
    bản thô là tách hồ sơ đúng theo kiểu mà lớp kiểm này sinh ra để chặn.
    """
    cach_viet = ["079075001234", "079 075 001234", "079.075.001234", "079-075-001234"]
    ra = {_sinh(gateway, citizen_id=c)["subject"]["identifier"]["value"] for c in cach_viet}
    assert ra == {"079075001234"}


def test_bhyt_thuong_hoa_van_quy_ve_mot(gateway):
    a = _sinh(gateway, insurance_card="gd4010120152431")
    b = _sinh(gateway, insurance_card="GD4010120152431")
    assert a["subject"]["identifier"] == b["subject"]["identifier"]


def test_hai_vien_gui_khac_cach_van_la_mot_benh_nhan(gateway, emr, monkeypatch):
    """Chốt đầu cuối: chuẩn hóa xong thì trục chỉ có MỘT hồ sơ, không phải hai."""
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)

    for ma_co_so, ma_benh_an, cccd in (("BV-A-001", "BN-A-1", "079 075 001234"),
                                       ("BV-B-002", "BN-B-1", "079075001234")):
        condition = gateway.convert_to_fhir(gateway.FHIRConvertRequest(
            **{**CHUNG, "patient_id": ma_benh_an}, citizen_id=cccd,
            facility_code=ma_co_so))
        gateway.sync_fhir_record({"condition": condition, "patient": {
            "id": ma_benh_an, "name": "Nguyễn Văn Hùng", "citizen_id": cccd}})

    assert len(emr.store["Patient"]) == 1
