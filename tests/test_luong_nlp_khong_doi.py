# -*- coding: utf-8 -*-
"""
Chốt chặn hồi quy: các chức năng thêm vào KHÔNG được đụng tới luồng NLP tự động.

Sửa chẩn đoán và khai mã cơ sở đều nằm ở phần sau khi mô hình đã cho kết quả.
Bài này giữ cho phần trước đó y nguyên: cùng câu chẩn đoán thì cùng mã, cùng độ
tin cậy, cùng quyết định "tự động liên thông hay dừng chờ bác sĩ duyệt".
"""
import pytest
from fastapi.testclient import TestClient

from conftest import FakeResponse

# Phản hồi của Gateway cho một câu có HAI bệnh, một mã trên ngưỡng tự động và một
# mã dưới ngưỡng. Cố định ở đây để bài test không phụ thuộc vào mô hình.
CHUAN_HOA = {
    "query": "đtđ tuýp 2 kèm cao huyết áp",
    "diagnoses": [
        {"fragment": "đtđ tuýp 2", "predictions": [
            {"code": "E11.9", "code_no_dot": "E119", "name_vi": "Đái tháo đường týp 2",
             "confidence": 93.4, "suggested_verification_status": "provisional",
             "requires_review": False}]},
        {"fragment": "cao huyết áp", "predictions": [
            {"code": "I10", "code_no_dot": "I10", "name_vi": "Tăng huyết áp vô căn",
             "confidence": 71.5, "suggested_verification_status": "unconfirmed",
             "requires_review": True}]},
    ],
    "predictions": [],
}

# Payload mà HIS gửi Gateway TRƯỚC khi có chức năng sửa chẩn đoán.
PAYLOAD_GOC = {
    "patient_id": "MRN-9",
    "patient_name": "Trần Thị B",
    "raw_clinical_note": "đtđ tuýp 2 kèm cao huyết áp",
    "icd10_code": "E11.9",
    "icd10_display": "Đái tháo đường týp 2",
    "confidence_score": 93.4,
    "clinical_status": "active",
    "gender": "Nữ",
    "birth_date": "1975-05-05",
    "citizen_id": "079075001234",
    "insurance_card": None,
}


@pytest.fixture
def da_dong_bo(make_his):
    da_goi = []

    def post(url, json=None, headers=None, timeout=None):
        da_goi.append((url, json))
        if url.endswith("/api/standardize"):
            return FakeResponse(CHUAN_HOA)
        if url.endswith("/api/fhir/condition"):
            return FakeResponse({"resourceType": "Condition", "id": "x"})
        return FakeResponse({"condition_id": "cond-1"})

    server = make_his("BV-DEMO-01", "Bệnh viện Demo SMIG", gateway_post=post)
    client = TestClient(server.app)
    client.post("/api/patients", json={
        "id": "MRN-9", "name": "Trần Thị B", "gender": "Nữ", "birth_date": "1975-05-05",
        "clinical_note": "đtđ tuýp 2 kèm cao huyết áp", "citizen_id": "079075001234"})
    return client.post("/api/sync/MRN-9").json(), da_goi


def test_ket_qua_nlp_giu_nguyen(da_dong_bo):
    ket_qua, _ = da_dong_bo
    chan_doan = ket_qua["conditions"]
    assert [c["code"] for c in chan_doan] == ["E11.9", "I10"]
    assert chan_doan[0]["confidence"] == 93.4
    assert chan_doan[0]["fragment"] == "đtđ tuýp 2"
    assert chan_doan[0]["verification_status"] == "provisional"


def test_chot_chan_an_toan_lam_sang_con_nguyen(da_dong_bo):
    """Mã dưới ngưỡng phải dừng chờ bác sĩ, không được tự đẩy lên trục."""
    ket_qua, _ = da_dong_bo
    duoi_nguong = ket_qua["conditions"][1]
    assert duoi_nguong["status"] == "Needs Review"
    assert duoi_nguong["fhir_condition_id"] is None
    assert ket_qua["status"] == "Needs Review"


def test_goi_standardize_bang_nguyen_van(da_dong_bo):
    _, da_goi = da_dong_bo
    truy_van = [j for u, j in da_goi if u.endswith("/api/standardize")]
    assert truy_van == [{"query": "đtđ tuýp 2 kèm cao huyết áp"}]


def test_payload_sinh_fhir_khong_doi(da_dong_bo):
    _, da_goi = da_dong_bo
    gui = [j for u, j in da_goi if u.endswith("/api/fhir/condition")]
    assert len(gui) == 1

    payload = dict(gui[0])
    # Ba trường được thêm, không trường nào đụng tới kết luận của mô hình:
    #   verification_status=None -> "không ghi đè", Gateway vẫn tự suy từ độ tin cậy
    #   facility_code/name       -> HIS khai mình là ai
    assert payload.pop("verification_status") is None
    assert payload.pop("facility_code") == "BV-DEMO-01"
    assert payload.pop("facility_name") == "Bệnh viện Demo SMIG"
    assert payload == PAYLOAD_GOC


def test_khai_dung_ma_co_so_cho_condition_y_het(gateway, monkeypatch):
    """Trường facility_code mới thêm không được làm đổi tài nguyên sinh ra."""
    monkeypatch.setattr(gateway, "FACILITY_CODE", "BV-DEMO-01")
    monkeypatch.setattr(gateway, "FACILITY_NAME", "Bệnh viện Demo SMIG")

    chung = dict(patient_id="MRN-9", patient_name="Trần Thị B",
                 raw_clinical_note="đtđ tuýp 2", icd10_code="E11.9",
                 icd10_display="Đái tháo đường týp 2", confidence_score=93.4,
                 citizen_id="079075001234", encounter_date="2026-08-15")

    cu = gateway.convert_to_fhir(gateway.FHIRConvertRequest(**chung))
    moi = gateway.convert_to_fhir(gateway.FHIRConvertRequest(
        **chung, facility_code="BV-DEMO-01", facility_name="Bệnh viện Demo SMIG"))
    cu.pop("recordedDate"), moi.pop("recordedDate")
    assert cu == moi
    assert moi["verificationStatus"]["coding"][0]["code"] == "provisional"
