# -*- coding: utf-8 -*-
"""
Một bản Gateway phục vụ nhiều bệnh viện.

Mô hình NLP chiếm vài GB RAM nên chạy hai bản Gateway trên một máy thử nghiệm là
quá nặng. HIS tự khai mã cơ sở trong từng yêu cầu để một bản Gateway phục vụ được
cả hai, nhưng chỉ khi Gateway bật cờ - mã cơ sở là danh tính của bên ghi hồ sơ.
"""
import pytest
from fastapi import HTTPException

CHUNG = dict(patient_name="Nguyễn Văn A", raw_clinical_note="đtđ tuýp 2",
             icd10_code="E11.9", icd10_display="Đái tháo đường týp 2",
             confidence_score=93.4, encounter_date="2026-08-15")


def _sinh(gateway, ma_benh_an, **them):
    return gateway.convert_to_fhir(
        gateway.FHIRConvertRequest(patient_id=ma_benh_an, **CHUNG, **them))


def _co_so(tai_nguyen):
    return tai_nguyen["meta"]["tag"][0]["code"]


def _khoa(tai_nguyen):
    return tai_nguyen["identifier"][0]["value"]


def test_khong_khai_ma_thi_lay_cau_hinh_gateway(gateway):
    """Đường đi của mọi bản HIS cũ - hành vi phải y nguyên."""
    assert _co_so(_sinh(gateway, "BN0005")) == "BV-A-001"


def test_khai_dung_ma_cua_gateway_cho_ket_qua_y_het(gateway):
    khong_khai = _sinh(gateway, "BN0005")
    co_khai = _sinh(gateway, "BN0005", facility_code="BV-A-001",
                    facility_name="Bệnh viện Đa khoa A")
    khong_khai.pop("recordedDate"), co_khai.pop("recordedDate")
    assert khong_khai == co_khai


def test_khai_ma_la_khi_chua_bat_co_thi_bi_chan(gateway):
    with pytest.raises(HTTPException) as loi:
        _sinh(gateway, "BN0005", facility_code="BV-B-002")
    assert loi.value.status_code == 403
    # Từ chối thẳng chứ không âm thầm lấy mã của Gateway: sai cấu hình mà vẫn
    # chạy thì hồ sơ viện B nằm trên trục dưới tên viện A và không ai phát hiện.
    assert "SMIG_ALLOW_CLIENT_FACILITY" in loi.value.detail


def test_hai_vien_trung_ma_benh_an_khong_bi_tron(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)
    a = _sinh(gateway, "BN0005", facility_code="BV-A-001", facility_name="Viện A")
    b = _sinh(gateway, "BN0005", facility_code="BV-B-002", facility_name="Viện B")

    # Hai người KHÁC nhau tình cờ trùng mã bệnh án thì không được gộp làm một.
    assert a["subject"]["identifier"] != b["subject"]["identifier"]
    assert _khoa(a) != _khoa(b)
    assert (_co_so(a), _co_so(b)) == ("BV-A-001", "BV-B-002")


def test_cung_cccd_thi_quy_ve_mot_nguoi(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)
    a = _sinh(gateway, "BN0005", facility_code="BV-A-001", citizen_id="079075001234")
    b = _sinh(gateway, "BN9999", facility_code="BV-B-002", citizen_id="079075001234")

    assert a["subject"]["identifier"] == b["subject"]["identifier"]
    # Nhưng vẫn là hai chẩn đoán riêng, ghi đúng tên từng nơi lập.
    assert _khoa(a) != _khoa(b)
    assert _co_so(a) != _co_so(b)


def test_dong_bo_len_emr_ghi_dung_co_so_cua_ben_goi(gateway, emr, monkeypatch):
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)
    condition = _sinh(gateway, "BN0005", facility_code="BV-B-002",
                      facility_name="Bệnh viện Đa khoa B")
    ket_qua = gateway.sync_fhir_record({"condition": condition, "patient": {
        "id": "BN0005", "name": "Nguyễn Văn A",
        "gender": "Nam", "birth_date": "1980-01-01"}})

    assert ket_qua["facility_code"] == "BV-B-002"
    to_chuc = list(emr.store["Organization"].values())[0]
    assert to_chuc["identifier"][0]["value"] == "BV-B-002"
    # Định danh bệnh nhân phải mang mã cơ sở của bên gọi. Lấy nhầm mã của Gateway
    # thì khối 'patient' và Condition.subject lệch nhau -> 422 oan.
    benh_nhan = list(emr.store["Patient"].values())[0]
    assert "BV-B-002" in benh_nhan["identifier"][0]["system"]


def test_tat_co_thi_khong_dong_bo_tai_nguyen_mang_ma_la(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)
    condition = _sinh(gateway, "BN0005", facility_code="BV-B-002")

    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", False)
    with pytest.raises(HTTPException) as loi:
        gateway.sync_fhir_record({"condition": condition})
    assert loi.value.status_code == 403
