# -*- coding: utf-8 -*-
"""
Bệnh nhân được bổ sung CCCD ở lần khám sau.

Đây là chuyện thường ngày: lần đầu tiếp nhận gấp, lễ tân chỉ kịp cấp mã bệnh án;
lần sau bệnh nhân mang giấy tờ tới thì CCCD mới được nhập vào. Cùng một người,
cùng một bệnh viện, chỉ khác ở chỗ hồ sơ đã đầy đủ hơn.

Khóa đồng nhất bệnh nhân KHÔNG được đổi theo việc HIS có gửi kèm CCCD hay không.
Đổi khóa giữa chừng thì trục sinh thêm một bệnh nhân thứ hai cho cùng một người,
và từ lần thứ ba trở đi conditional update khớp cả hai bản nên máy chủ trả 412 -
luồng liên thông chết hẳn mà không tự phục hồi được.
"""
import pytest
from fastapi import HTTPException

CCCD = "079075001234"
CHUNG = dict(patient_name="Phạm Thị D", raw_clinical_note="đtđ tuýp 2",
             icd10_code="E11.9", icd10_display="Đái tháo đường týp 2",
             confidence_score=93.0, encounter_date="2026-08-15")


@pytest.fixture
def kham(gateway):
    """Một lần khám của cùng bệnh nhân BN-100 tại BV-A-001."""
    def _kham(citizen_id=None):
        condition = gateway.convert_to_fhir(gateway.FHIRConvertRequest(
            patient_id="BN-100", citizen_id=citizen_id, **CHUNG))
        ho_so = {"id": "BN-100", "name": "Phạm Thị D",
                 "gender": "Nữ", "birth_date": "1982-04-11"}
        if citizen_id:
            ho_so["citizen_id"] = citizen_id
        return gateway.sync_fhir_record({"condition": condition, "patient": ho_so})
    return _kham


def test_bo_sung_cccd_khong_de_ra_benh_nhan_thu_hai(kham, emr):
    kham()                    # lần 1: chưa có CCCD
    kham(citizen_id=CCCD)     # lần 2: đã bổ sung CCCD

    assert len(emr.store["Patient"]) == 1, (
        "Bổ sung CCCD làm trục đẻ thêm một bệnh nhân cho cùng một người")


def test_lan_kham_thu_ba_van_dong_bo_duoc(kham, emr):
    """
    Hai bản Patient cùng mang mã bệnh án 'BN-100' thì lần đồng bộ sau tra theo mã
    đó sẽ khớp cả hai, và máy chủ từ chối sửa. Đây mới là hậu quả nặng: không chỉ
    sai dữ liệu mà còn kẹt luôn đường liên thông.
    """
    kham()
    kham(citizen_id=CCCD)
    kham()                    # lần 3: lễ tân lại quên nhập CCCD

    assert len(emr.store["Patient"]) == 1


def test_key_condition_khong_doi_khi_bo_sung_cccd(kham, emr):
    """
    Cùng người, cùng bệnh, cùng ngày, cùng viện thì phải là MỘT chẩn đoán. Khóa
    nghiệp vụ bám theo định danh nào cũng được, miễn là định danh đó không đổi
    giữa hai lần khám - nếu không thì trục có hai bản ghi cho một chẩn đoán.
    """
    a = kham()
    b = kham(citizen_id=CCCD)

    assert a["condition_key"] == b["condition_key"]
    assert len(emr.store["Condition"]) == 1


def test_hai_nguoi_that_su_khac_nhau_van_tach_rieng(gateway, emr):
    """Chốt chiều ngược lại: sửa xong không được gộp nhầm hai người thành một."""
    for ma_benh_an, cccd in (("BN-100", "079075001234"), ("BN-200", "079075009999")):
        condition = gateway.convert_to_fhir(gateway.FHIRConvertRequest(
            patient_id=ma_benh_an, citizen_id=cccd, **CHUNG))
        gateway.sync_fhir_record({"condition": condition, "patient": {
            "id": ma_benh_an, "name": "X", "citizen_id": cccd}})

    assert len(emr.store["Patient"]) == 2
    assert len(emr.store["Condition"]) == 2
