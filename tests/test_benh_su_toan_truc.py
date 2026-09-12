# -*- coding: utf-8 -*-
"""
Bệnh sử đầy đủ của một bệnh nhân: nội viện cộng mọi tuyến khác trên trục.

`GET /api/patients` cố ý chỉ trả bệnh án cục bộ, nên bác sĩ mở hồ sơ một bệnh
nhân ĐÃ tiếp nhận tại đây thì chỉ thấy phần viện mình ghi - chẩn đoán do tuyến
khác lập vẫn nằm trên trục mà không hiện ra ở đâu. Đường tra bằng CCCD hiện có
chỉ chạy khi hồ sơ CHƯA có ở đây, nên ca đã tiếp nhận lại là ca mù nhất.

Bộ này chốt bốn thứ: thấy được tuyến khác, không đếm đôi chẩn đoán đã liên
thông, tách được phần chưa lên trục, và nói đúng lý do khi danh sách rỗng.
"""
import pytest

CCCD = "079075001234"


class EmrGia:
    """Trục dữ liệu tối giản: chỉ phục vụ tra Patient theo identifier và Condition."""

    def __init__(self, patients=None, conditions=None, offline=False):
        self.patients = patients or {}      # {"he|gia_tri": {"id": ...}}
        self.conditions = conditions or []
        self.offline = offline
        self.da_hoi = []

    def get(self, url, params=None, headers=None, timeout=None):
        from conftest import FakeResponse
        if self.offline:
            import requests
            raise requests.ConnectionError("EMR Cloud offline")
        params = params or {}
        if "/Patient" in url:
            key = params.get("identifier", "")
            self.da_hoi.append(key)
            hit = self.patients.get(key)
            return FakeResponse({"entry": [{"resource": hit}] if hit else []})
        if "/Condition" in url:
            return FakeResponse({"entry": [{"resource": c} for c in self.conditions]})
        return FakeResponse({}, 404)


def condition(cid, ma, ten, co_so, ten_co_so, ngay="2026-08-20"):
    return {
        "id": cid,
        "code": {"coding": [{"code": ma, "display": ten}]},
        "note": [{"text": "ghi chu " + ma}],
        "recordedDate": ngay,
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "meta": {"tag": [{
            "system": "https://smig.nckh.vn/fhir/identifier/co-so-kcb",
            "code": co_so, "display": ten_co_so,
        }]},
    }


@pytest.fixture
def his(make_his):
    """HIS của bệnh viện A, có sẵn một bệnh nhân mang CCCD."""
    server = make_his(facility_code="BV-A-001", facility_name="Bệnh viện Đa khoa A")
    with server.db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO patients (id, name, gender, birth_date, clinical_note,"
            " citizen_id, sync_status) VALUES (?,?,?,?,?,?,?)",
            ("MRN-1", "Lê Văn Cường", "Nam", "1975-03-02", "đtđ tuýp 2", CCCD, "Synced"))
    return server


def _them_chan_doan_cuc_bo(server, ma, fhir_id, **kw):
    with server.db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO patient_conditions (patient_id, icd10_code, icd10_display,"
            " fragment, confidence_score, verification_status, fhir_condition_id,"
            " status, clinical_status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("MRN-1", ma, kw.get("ten", ma), kw.get("fragment", "mo ta"),
             kw.get("diem", 93.0), "provisional", fhir_id,
             kw.get("status", "Synced"), "active"))


def test_thay_duoc_chan_doan_cua_tuyen_khac(his, monkeypatch):
    """Điều chính: hồ sơ đã tiếp nhận tại đây vẫn phải thấy bệnh sử nơi khác."""
    emr = EmrGia(
        patients={f"https://smig.nckh.vn/fhir/identifier/cccd|{CCCD}": {"id": "p-1"}},
        conditions=[
            condition("c-1", "E11.9", "Đái tháo đường týp 2", "BV-A-001", "Bệnh viện Đa khoa A"),
            condition("c-2", "N18.9", "Bệnh thận mạn", "BV-B-002", "Bệnh viện Đa khoa B"),
        ])
    monkeypatch.setattr(his.requests, "get", emr.get)
    _them_chan_doan_cuc_bo(his, "E11.9", "c-1")

    from fastapi.testclient import TestClient
    kq = TestClient(his.app).get("/api/patients/MRN-1/history").json()

    assert kq["emr_online"] is True
    assert kq["tra_cuu_bang"] == "cccd"
    assert kq["so_co_so"] == 2
    assert kq["so_co_so_ngoai_vien"] == 1

    # Viện mình luôn đứng trước, tuyến khác xếp sau.
    assert [g["facility_code"] for g in kq["theo_co_so"]] == ["BV-A-001", "BV-B-002"]
    ngoai = kq["theo_co_so"][1]
    assert ngoai["la_ngoai_vien"] is True
    assert ngoai["facility_name"] == "Bệnh viện Đa khoa B"
    assert [c["icd10_code"] for c in ngoai["chan_doan"]] == ["N18.9"]


def test_khong_dem_doi_chan_doan_da_lien_thong(his, monkeypatch):
    """
    Chẩn đoán đã đẩy lên trục nằm ở CẢ bệnh án cục bộ lẫn trục.

    Không khử trùng thì nó hiện hai lần và bác sĩ đọc thành bệnh nhân mắc bệnh đó
    hai lần - kiểu sai âm thầm và nguy hiểm hơn hẳn việc thiếu dữ liệu.
    """
    emr = EmrGia(
        patients={f"https://smig.nckh.vn/fhir/identifier/cccd|{CCCD}": {"id": "p-1"}},
        conditions=[condition("c-1", "E11.9", "Đái tháo đường týp 2",
                              "BV-A-001", "Bệnh viện Đa khoa A")])
    monkeypatch.setattr(his.requests, "get", emr.get)
    _them_chan_doan_cuc_bo(his, "E11.9", "c-1")

    from fastapi.testclient import TestClient
    kq = TestClient(his.app).get("/api/patients/MRN-1/history").json()

    noi_vien = kq["theo_co_so"][0]
    assert noi_vien["so_chan_doan"] == 1, "E11.9 bị đếm hai lần"
    assert kq["tong_tren_truc"] == 1
    assert kq["chua_lien_thong"] == []
    # Bản trên trục phải được đánh dấu là còn trong bệnh án cục bộ, nhờ đó giao
    # diện biết chẩn đoán nào sửa được và chẩn đoán nào chỉ đọc.
    assert noi_vien["chan_doan"][0]["co_ban_cuc_bo"] is True


def test_tach_rieng_chan_doan_chua_len_truc(his, monkeypatch):
    """Chưa liên thông thì tuyến khác chưa đọc được - không được xếp lẫn vào."""
    emr = EmrGia(
        patients={f"https://smig.nckh.vn/fhir/identifier/cccd|{CCCD}": {"id": "p-1"}},
        conditions=[condition("c-1", "E11.9", "Đái tháo đường týp 2",
                              "BV-A-001", "Bệnh viện Đa khoa A")])
    monkeypatch.setattr(his.requests, "get", emr.get)
    _them_chan_doan_cuc_bo(his, "E11.9", "c-1")
    _them_chan_doan_cuc_bo(his, "I10", None, ten="Tăng huyết áp", status="Unsynced")

    from fastapi.testclient import TestClient
    kq = TestClient(his.app).get("/api/patients/MRN-1/history").json()

    assert [c["icd10_code"] for c in kq["chua_lien_thong"]] == ["I10"]
    assert kq["theo_co_so"][0]["so_chan_doan"] == 1, "I10 chưa lên trục mà đã xếp vào cơ sở"


def test_emr_offline_van_tra_phan_cuc_bo(his, monkeypatch):
    """Xem bệnh sử là thao tác đọc - trục hỏng không được làm hỏng cả màn hình."""
    monkeypatch.setattr(his.requests, "get", EmrGia(offline=True).get)
    _them_chan_doan_cuc_bo(his, "I10", None, ten="Tăng huyết áp", status="Unsynced")

    from fastapi.testclient import TestClient
    res = TestClient(his.app).get("/api/patients/MRN-1/history")

    assert res.status_code == 200
    kq = res.json()
    assert kq["emr_online"] is False
    assert [c["icd10_code"] for c in kq["chua_lien_thong"]] == ["I10"]
    assert "không kết nối được emr cloud" in kq["canh_bao"].lower()


def test_thieu_dinh_danh_toan_quoc_thi_noi_ro_ly_do(make_his, monkeypatch):
    """
    Ba lý do khiến danh sách rỗng dẫn tới ba hành động khác nhau của bác sĩ.

    Trục không chạy, bệnh nhân chưa có CCCD/BHYT, hay đúng là chưa ai ghi gì -
    gộp cả ba thành "không có dữ liệu" là bỏ mất đúng thông tin cần nhất.
    """
    server = make_his(facility_code="BV-A-001", facility_name="Bệnh viện Đa khoa A")
    with server.db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO patients (id, name, gender, birth_date, clinical_note,"
            " sync_status) VALUES (?,?,?,?,?,?)",
            ("MRN-9", "Trần Thị D", "Nữ", "1990-01-01", "đau đầu", "Unsynced"))

    emr = EmrGia(patients={})
    monkeypatch.setattr(server.requests, "get", emr.get)

    from fastapi.testclient import TestClient
    kq = TestClient(server.app).get("/api/patients/MRN-9/history").json()

    assert "cccd" in kq["canh_bao"].lower() or "bhyt" in kq["canh_bao"].lower()
    # Không có định danh toàn quốc thì chỉ được hỏi theo mã bệnh án.
    assert all("identifier/mrn" in k for k in emr.da_hoi), emr.da_hoi


def test_uu_tien_cccd_hon_ma_benh_an(his, monkeypatch):
    """
    Thứ tự tra ngược với `get_patients`, và đó là chủ ý.

    Ở đây ta đã cầm hồ sơ nên cần định danh quét rộng nhất. Hỏi mã bệnh án trước
    là tự giới hạn kết quả vào đúng phần vốn đã thấy - tức mất hẳn tuyến khác.
    """
    emr = EmrGia(patients={f"https://smig.nckh.vn/fhir/identifier/cccd|{CCCD}": {"id": "p-1"}})
    monkeypatch.setattr(his.requests, "get", emr.get)

    from fastapi.testclient import TestClient
    TestClient(his.app).get("/api/patients/MRN-1/history")

    assert emr.da_hoi, "Không hỏi trục lần nào"
    assert "identifier/cccd" in emr.da_hoi[0], f"Hỏi sai thứ tự: {emr.da_hoi}"


# --- Ho so CHUA tiep nhan tai day ------------------------------------------
# Doi bat buoc phai co benh an cuc bo thi dung ca can nhat - benh nhan vua
# chuyen tuyen toi, chua kip tiep nhan - lai la ca duy nhat khong xem duoc benh
# su. Ho chua tung kham o day nen vien nay khong co ma benh an cho ho.

MRN_KHAC = "https://smig.nckh.vn/fhir/identifier/mrn/BV-B-002"


@pytest.fixture
def his_trong(make_his):
    """HIS chua tiep nhan bat ky ai."""
    return make_his(facility_code="BV-A-001", facility_name="Bệnh viện Đa khoa A")


@pytest.fixture
def truc_co_benh_nhan():
    """Trục có sẵn một bệnh nhân của bệnh viện B, mang CCCD và thẻ BHYT."""
    return EmrGia(
        patients={
            f"https://smig.nckh.vn/fhir/identifier/cccd|{CCCD}": {
                "id": "p-9",
                "name": [{"text": "Lê Văn Cường"}],
                "gender": "male",
                "birthDate": "1975-03-02",
                "identifier": [
                    {"system": "https://smig.nckh.vn/fhir/identifier/cccd", "value": CCCD},
                    {"system": MRN_KHAC, "value": "BN-B-77"},
                ],
            },
        },
        conditions=[
            condition("c-9", "N18.9", "Bệnh thận mạn", "BV-B-002", "Bệnh viện Đa khoa B"),
        ])


def test_doc_duoc_benh_su_ngoai_vien_khi_chua_tiep_nhan(
        his_trong, truc_co_benh_nhan, monkeypatch):
    """Ca chuyển tuyến: chưa có bệnh án ở đây nhưng phải đọc được bệnh sử."""
    monkeypatch.setattr(his_trong.requests, "get", truc_co_benh_nhan.get)

    from fastapi.testclient import TestClient
    res = TestClient(his_trong.app).get(f"/api/patients/{CCCD}/history")

    assert res.status_code == 200, res.text
    kq = res.json()
    assert kq["co_ho_so_cuc_bo"] is False
    assert kq["tra_cuu_bang"] == "cccd"
    assert kq["so_co_so_ngoai_vien"] == 1
    assert kq["theo_co_so"][0]["facility_name"] == "Bệnh viện Đa khoa B"
    assert [c["icd10_code"] for c in kq["theo_co_so"][0]["chan_doan"]] == ["N18.9"]
    # Thông tin hành chính lấy từ trục, vì không có bản cục bộ nào để ưu tiên.
    assert kq["patient"]["name"] == "Lê Văn Cường"
    assert kq["patient"]["gender"] == "Nam"


def test_hien_ma_benh_an_noi_khac_kem_ten_co_so(
        his_trong, truc_co_benh_nhan, monkeypatch):
    """
    Mã bệnh án nơi khác phải LUÔN đi kèm cơ sở đã cấp.

    Hiện trần mã thì vô nghĩa và còn nguy hiểm: "BN-B-77" của viện này và viện kia
    là hai người khác nhau. Kèm cơ sở thì bác sĩ gọi sang nơi đó hỏi được bệnh án
    gốc theo đúng mã họ đang giữ.
    """
    monkeypatch.setattr(his_trong.requests, "get", truc_co_benh_nhan.get)

    from fastapi.testclient import TestClient
    kq = TestClient(his_trong.app).get(f"/api/patients/{CCCD}/history").json()

    assert kq["patient"]["ma_benh_an_noi_khac"] == [
        {"facility_code": "BV-B-002", "value": "BN-B-77"}]


def test_ma_benh_an_cua_vien_khac_khong_tra_ra(his_trong, truc_co_benh_nhan, monkeypatch):
    """
    Tra bằng mã bệnh án của viện KHÁC phải trượt, và đó là hành vi đúng.

    Mã bệnh án chỉ có nghĩa trong nội bộ nơi cấp nó. Quét mã trần qua mọi cơ sở
    chính là lỗi trộn hồ sơ mà cả thiết kế này sinh ra để chặn - "BN-B-77" ở hai
    viện là hai người. Thông báo phải chỉ đường sang CCCD/BHYT.
    """
    monkeypatch.setattr(his_trong.requests, "get", truc_co_benh_nhan.get)

    from fastapi.testclient import TestClient
    res = TestClient(his_trong.app).get("/api/patients/BN-B-77/history")

    assert res.status_code == 404
    assert "cccd" in res.json()["detail"].lower()


def test_khong_co_o_dau_ca_thi_404(his_trong, monkeypatch):
    monkeypatch.setattr(his_trong.requests, "get", EmrGia(patients={}).get)
    from fastapi.testclient import TestClient
    assert TestClient(his_trong.app).get("/api/patients/KHONG-CO/history").status_code == 404
