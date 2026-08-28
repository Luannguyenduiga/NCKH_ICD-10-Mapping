# -*- coding: utf-8 -*-
"""
Xóa dữ liệu trên trục phải dừng trong phạm vi cơ sở của mình.

Cả thiết kế liên thông này dựng lên để chẩn đoán của bệnh viện A và B không lẫn
vào nhau. Đường GHI đã chặn kỹ (mã cơ sở nằm trong khóa nghiệp vụ, `meta.tag`
ghi rõ nơi lập). Đường XÓA thì không - và một nút "dọn dữ liệu demo" xóa mất
bệnh án của viện khác thì hậu quả nặng hơn ghi nhầm, vì không phục hồi được.
"""
import pytest
from fastapi import HTTPException

CHUNG = dict(patient_name="Vũ Văn E", raw_clinical_note="đtđ tuýp 2",
             icd10_code="E11.9", icd10_display="Đái tháo đường týp 2",
             confidence_score=93.0, encounter_date="2026-08-15")


@pytest.fixture
def hai_vien(gateway, emr, monkeypatch):
    """Trên trục có sẵn chẩn đoán của cả hai bệnh viện."""
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)

    def lien_thong(ma_co_so, ten_co_so, ma_benh_an, ma_icd):
        condition = gateway.convert_to_fhir(gateway.FHIRConvertRequest(
            patient_id=ma_benh_an, facility_code=ma_co_so, facility_name=ten_co_so,
            **{**CHUNG, "icd10_code": ma_icd}))
        gateway.sync_fhir_record({"condition": condition, "patient": {
            "id": ma_benh_an, "name": "Vũ Văn E"}})

    lien_thong("BV-A-001", "Bệnh viện Đa khoa A", "BN-A-1", "E11.9")
    lien_thong("BV-B-002", "Bệnh viện Đa khoa B", "BN-B-1", "N18.9")
    return gateway, emr


def test_xoa_khong_dung_den_ban_ghi_cua_vien_khac(hai_vien):
    gateway, emr = hai_vien
    assert emr.facilities() == ["BV-A-001", "BV-B-002"]

    # Gateway đang cấu hình là BV-A-001, nên bấm xóa chỉ được dọn phần của A.
    ket_qua = gateway.delete_synced_records()

    assert ket_qua["deleted"] == 1
    assert ket_qua["facility_code"] == "BV-A-001"
    assert emr.facilities() == ["BV-B-002"], "Đã xóa mất chẩn đoán của bệnh viện khác"
    assert emr.codes() == ["N18.9"]


def test_khong_con_gi_de_xoa_thi_bao_dung_pham_vi(hai_vien):
    gateway, _ = hai_vien
    gateway.delete_synced_records()
    lan_hai = gateway.delete_synced_records()

    assert lan_hai["deleted"] == 0
    assert lan_hai["remaining"] == 0
    # Không được nói "trục trống" khi phần của viện khác vẫn còn nguyên.
    assert "BV-A-001" in lan_hai["message"]


def test_xoa_ho_vien_khac_phai_theo_dung_chinh_sach_ma_co_so(hai_vien, monkeypatch):
    """Cùng một luật với đường ghi: chưa bật cờ nhiều cơ sở thì không khai mã lạ."""
    gateway, emr = hai_vien

    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", False)
    with pytest.raises(HTTPException) as loi:
        gateway.delete_synced_records(facility="BV-B-002")
    assert loi.value.status_code == 403
    assert emr.facilities() == ["BV-A-001", "BV-B-002"]

    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)
    assert gateway.delete_synced_records(facility="BV-B-002")["deleted"] == 1
    assert emr.facilities() == ["BV-A-001"]
