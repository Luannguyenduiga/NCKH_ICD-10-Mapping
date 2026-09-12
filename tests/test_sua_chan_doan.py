# -*- coding: utf-8 -*-
"""
Bác sĩ sửa lại chẩn đoán ĐÃ ghi nhận.

Trước đây bệnh án chỉ thêm được chẩn đoán: mô hình NLP suy sai mã thì mã sai nằm
lại vĩnh viễn, bác sĩ chỉ còn cách xóa cả hồ sơ rồi nhập lại từ đầu.
"""
import pytest
from fastapi.testclient import TestClient

from conftest import FakeResponse


class GatewayGia:
    """Gateway giả lập: chuẩn hóa ra một mã, và cấp id Condition tăng dần."""

    def __init__(self, ma="E11.9", ten="Đái tháo đường týp 2", do_tin_cay=95.0):
        self.ma, self.ten, self.do_tin_cay = ma, ten, do_tin_cay
        self.da_goi = []
        self._seq = 0

    def post(self, url, json=None, headers=None, timeout=None):
        self.da_goi.append(("POST", url, json))
        if url.endswith("/api/standardize"):
            return FakeResponse({"diagnoses": [{
                "fragment": json["query"],
                "predictions": [{
                    "code": self.ma, "code_no_dot": self.ma.replace(".", ""),
                    "name_vi": self.ten, "confidence": self.do_tin_cay,
                    "suggested_verification_status": "provisional",
                    "requires_review": False}],
            }]})
        if url.endswith("/api/fhir/condition"):
            return FakeResponse({"resourceType": "Condition"})
        self._seq += 1
        return FakeResponse({"condition_id": f"cond-{self._seq}"})

    def delete(self, url, params=None, headers=None, timeout=None):
        # Ghi lại `params` chứ không bỏ đi: mã cơ sở đi trong tham số truy vấn,
        # và Gateway dựa vào nó để biết ai đang gỡ.
        self.da_goi.append(("DELETE", url, params))
        return FakeResponse({"status": "success"})


@pytest.fixture
def his(make_his, monkeypatch):
    gw = GatewayGia()
    server = make_his(gateway_post=gw.post)
    monkeypatch.setattr(server.requests, "delete", gw.delete)
    client = TestClient(server.app)
    client.post("/api/patients", json={
        "id": "MRN-1", "name": "Nguyễn Văn A", "gender": "Nam",
        "birth_date": "1980-01-01", "clinical_note": "đtđ tuýp 2"})
    client.post("/api/sync/MRN-1")
    return client, gw, server


def _ho_so(client, ma_benh_nhan="MRN-1"):
    hs = [p for p in client.get("/api/patients").json() if p["id"] == ma_benh_nhan][0]
    return hs, hs["conditions"]


def test_ho_so_ban_dau(his):
    client, _, _ = his
    ho_so, chan_doan = _ho_so(client)
    assert [c["icd10_code"] for c in chan_doan] == ["E11.9"]
    assert chan_doan[0]["clinical_status"] == "active"
    assert ho_so["is_local"] is True


def test_sua_ma_sai_thanh_ma_dung(his):
    client, gw, _ = his
    res = client.put("/api/patients/MRN-1/conditions/E11.9", json={
        "icd10_code": "I10", "icd10_display": "Tăng huyết áp vô căn",
        "clinical_status": "active", "fragment": "tăng huyết áp vô căn"})
    assert res.status_code == 200, res.text
    data = res.json()

    # Bản mang mã sai phải được gỡ khỏi trục, nếu không hồ sơ mang cả mã đúng lẫn
    # mã sai và bên nhận không biết tin cái nào.
    assert data["retired_condition_id"] == "cond-1"
    assert any(u.endswith("/api/fhir/condition/cond-1")
               for kieu, u, _ in gw.da_goi if kieu == "DELETE")

    ho_so, chan_doan = _ho_so(client)
    assert [c["icd10_code"] for c in chan_doan] == ["I10"]
    # Bác sĩ tự chọn mã thì con số này là quyết định chuyên môn, không phải điểm
    # của mô hình - và nhờ vậy mã không bị đem so lại với ngưỡng tự động.
    assert chan_doan[0]["confidence_score"] == 100.0
    assert chan_doan[0]["verification_status"] == "confirmed"
    # Bảng bệnh án chỉ giữ một chẩn đoán chính, phải theo kịp thay đổi.
    assert ho_so["icd10_code"] == "I10" and ho_so["sync_status"] == "Synced"


def test_khong_go_nham_ban_ghi_vua_ghi(his):
    """Giữ nguyên mã trong cùng ngày -> khóa nghiệp vụ không đổi, không được xóa."""
    client, gw, _ = his
    res = client.put("/api/patients/MRN-1/conditions/E11.9", json={
        "icd10_code": "E11.9", "icd10_display": "Đái tháo đường týp 2",
        "clinical_status": "resolved"})
    assert res.status_code == 200, res.text
    # Gateway giả lập cấp id mới mỗi lần nên có gỡ; điều cần chắc là chỉ gỡ đúng
    # bản CŨ, không bao giờ gỡ bản vừa đẩy lên.
    moi = res.json()["fhir_condition_id"]
    assert not any(u.endswith(f"/api/fhir/condition/{moi}")
                   for kieu, u, _ in gw.da_goi if kieu == "DELETE")


def test_doi_trang_thai_lam_sang_giu_nguyen_mo_ta(his):
    client, _, _ = his
    client.put("/api/patients/MRN-1/conditions/E11.9", json={
        "icd10_code": "E11.9", "icd10_display": "Đái tháo đường týp 2",
        "clinical_status": "active", "fragment": "đái tháo đường týp 2"})
    client.put("/api/patients/MRN-1/conditions/E11.9", json={
        "icd10_code": "E11.9", "icd10_display": "Đái tháo đường týp 2",
        "clinical_status": "resolved"})

    _, chan_doan = _ho_so(client)
    assert chan_doan[0]["clinical_status"] == "resolved"
    # Bỏ trống `fragment` là "giữ nguyên", không phải "xóa đi".
    assert chan_doan[0]["fragment"] == "đái tháo đường týp 2"


def test_chan_trang_thai_khong_hop_le(his):
    client, _, _ = his
    res = client.put("/api/patients/MRN-1/conditions/E11.9", json={
        "icd10_code": "E11.9", "icd10_display": "x", "clinical_status": "khong-co-that"})
    assert res.status_code == 422
    assert "không hợp lệ" in res.json()["detail"]


def test_sua_ma_khong_co_trong_ho_so(his):
    client, _, _ = his
    res = client.put("/api/patients/MRN-1/conditions/Z99.9", json={
        "icd10_code": "I10", "icd10_display": "x"})
    assert res.status_code == 404


def test_go_chan_doan_va_tinh_lai_chan_doan_chinh(his):
    client, gw, _ = his
    gw.ma, gw.ten = "I10", "Tăng huyết áp vô căn"
    client.post("/api/patients/MRN-1/diagnosis", json={"clinical_note": "cao huyết áp"})
    _, chan_doan = _ho_so(client)
    assert [c["icd10_code"] for c in chan_doan] == ["E11.9", "I10"]

    assert client.delete("/api/patients/MRN-1/conditions/E11.9").status_code == 200
    ho_so, chan_doan = _ho_so(client)
    assert [c["icd10_code"] for c in chan_doan] == ["I10"]
    assert ho_so["icd10_code"] == "I10"

    # Gỡ nốt chẩn đoán cuối: hồ sơ phải quay về "chưa liên thông", không được để
    # lại mã đã bị gỡ ở cột chẩn đoán chính.
    assert client.delete("/api/patients/MRN-1/conditions/I10").status_code == 200
    ho_so, chan_doan = _ho_so(client)
    assert chan_doan == []
    assert ho_so["icd10_code"] is None and ho_so["sync_status"] == "Unsynced"


def test_sua_giu_nguyen_vi_tri_trong_danh_sach(his):
    """Sửa mã đầu tiên mà nó nhảy xuống cuối là đổi cả chẩn đoán chính của hồ sơ."""
    client, gw, _ = his
    gw.ma, gw.ten = "I10", "Tăng huyết áp vô căn"
    client.post("/api/patients/MRN-1/diagnosis", json={"clinical_note": "cao huyết áp"})

    client.put("/api/patients/MRN-1/conditions/E11.9", json={
        "icd10_code": "E10.9", "icd10_display": "Đái tháo đường týp 1",
        "clinical_status": "active"})
    _, chan_doan = _ho_so(client)
    assert [c["icd10_code"] for c in chan_doan] == ["E10.9", "I10"]


def test_go_ban_ghi_cu_co_khai_ma_co_so(his):
    """
    HIS phải tự khai mình là ai khi nhờ Gateway gỡ bản ghi.

    Gateway chỉ cho gỡ chẩn đoán do chính cơ sở đó lập. Thiếu tham số này thì ở
    chế độ một Gateway phục vụ nhiều bệnh viện, HIS bị 403 khi gỡ đúng bản ghi
    của mình - và lỗi hiện ra dưới dạng bản ghi thừa còn sót trên trục, rất khó
    lần ra nguyên nhân.
    """
    client, gw, server = his
    res = client.put("/api/patients/MRN-1/conditions/E11.9", json={
        "icd10_code": "I10", "icd10_display": "Tăng huyết áp vô căn",
        "clinical_status": "active", "fragment": "tăng huyết áp vô căn"})
    assert res.status_code == 200, res.text

    goi_xoa = [p for kieu, _, p in gw.da_goi if kieu == "DELETE"]
    assert goi_xoa, "Sửa mã phải kéo theo một lệnh gỡ bản ghi cũ"
    assert all(p and p.get("facility") == server.FACILITY_CODE for p in goi_xoa), (
        f"Lệnh gỡ thiếu mã cơ sở: {goi_xoa}")
