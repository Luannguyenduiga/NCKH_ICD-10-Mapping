# -*- coding: utf-8 -*-
"""
Danh sách bệnh án phải hiện luôn chẩn đoán của tuyến khác, kèm nơi khám.

Bản trước, nhánh không có `search_id` cố ý chỉ trả bệnh án cục bộ. Đó là chỗ
hỏng về bản chất: một hệ thống liên thông mà màn hình chính vẫn chỉ thấy phần
của mình thì phần liên thông chỉ tồn tại trong các màn phụ. Bác sĩ mở bệnh án ra
phải thấy ngay bệnh nhân đã khám gì ở đâu.

Kèm theo là hai chốt ngược chiều: không được đếm đôi, và không được để mất dấu
ai đã ghi bản nào.
"""
import pytest

CCCD = "079075001234"
NS = "https://smig.nckh.vn/fhir"


class TrucGia:
    """Trục trả về toàn bộ Patient và Condition trong hai lần gọi."""

    def __init__(self, patients=None, conditions=None, offline=False):
        self.patients = patients or []
        self.conditions = conditions or []
        self.offline = offline
        self.so_lan_goi = 0

    def get(self, url, params=None, headers=None, timeout=None):
        from conftest import FakeResponse
        if self.offline:
            import requests
            raise requests.ConnectionError("offline")
        self.so_lan_goi += 1
        if "/Patient" in url:
            return FakeResponse({"entry": [{"resource": r} for r in self.patients]})
        if "/Condition" in url:
            return FakeResponse({"entry": [{"resource": r} for r in self.conditions]})
        return FakeResponse({}, 404)


def cond(cid, ma, ten, co_so, ten_co_so, emr_pid="p-1"):
    return {
        "id": cid,
        "subject": {"reference": "Patient/" + emr_pid},
        "code": {"coding": [{"code": ma, "display": ten}]},
        "note": [{"text": "mo ta " + ma}],
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "meta": {"tag": [{"system": NS + "/identifier/co-so-kcb",
                          "code": co_so, "display": ten_co_so}]},
    }


def ho_so_tren_truc(emr_pid="p-1", cccd=CCCD):
    return {"id": emr_pid,
            "identifier": [{"system": NS + "/identifier/cccd", "value": cccd}]}


def _ho_so(server):
    from fastapi.testclient import TestClient
    return TestClient(server.app).get("/api/patients")


@pytest.fixture
def his_va_truc(make_his, monkeypatch):
    """Bệnh viện A đã tiếp nhận một bệnh nhân; trên trục bệnh viện B cũng có chẩn đoán."""
    truc = TrucGia(
        patients=[ho_so_tren_truc()],
        conditions=[
            cond("c-a", "E11.9", "Đái tháo đường týp 2", "BV-A-001", "Bệnh viện A"),
            cond("c-b", "N18.9", "Bệnh thận mạn", "BV-B-002", "Bệnh viện B"),
        ])
    server = make_his(facility_code="BV-A-001", facility_name="Bệnh viện A")
    monkeypatch.setattr(server.requests, "get", truc.get)
    with server.db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO patients (id,name,gender,birth_date,clinical_note,"
            "citizen_id,sync_status) VALUES (?,?,?,?,?,?,?)",
            ("MRN-1", "Lê Văn Cường", "Nam", "1975-03-02", "đtđ", CCCD, "Synced"))
        cur.execute(
            "INSERT INTO patient_conditions (patient_id,icd10_code,icd10_display,"
            "fragment,confidence_score,verification_status,fhir_condition_id,"
            "status,clinical_status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("MRN-1", "E11.9", "Đái tháo đường týp 2", "đtđ", 93.0,
             "provisional", "c-a", "Synced", "active"))
    return server, truc


def test_danh_sach_hien_luon_chan_doan_tuyen_khac(his_va_truc):
    """Điều chính: không phải mở màn phụ mới thấy bệnh sử nơi khác."""
    server, _ = his_va_truc
    p = _ho_so(server).json()[0]

    ma = {c["icd10_code"] for c in p["conditions"]}
    assert ma == {"E11.9", "N18.9"}, "Thiếu chẩn đoán tuyến khác: %s" % ma
    assert p["so_chan_doan_ngoai_vien"] == 1


def test_moi_chan_doan_deu_noi_ro_noi_lap(his_va_truc):
    """
    Không chẩn đoán nào được để trống nơi lập - kể cả của chính viện này.

    Thấy bệnh sử mà không rõ ai ghi còn nguy hiểm hơn không thấy: bác sĩ mặc định
    coi đó là bản ghi của viện mình và tin theo mức không đúng.
    """
    server, _ = his_va_truc
    p = _ho_so(server).json()[0]

    for c in p["conditions"]:
        assert c.get("facility_name"), "Chẩn đoán %s không rõ nơi lập" % c["icd10_code"]
        assert "la_ngoai_vien" in c

    theo_ma = {c["icd10_code"]: c for c in p["conditions"]}
    assert theo_ma["E11.9"]["la_ngoai_vien"] is False
    assert theo_ma["N18.9"]["la_ngoai_vien"] is True
    assert theo_ma["N18.9"]["facility_name"] == "Bệnh viện B"
    # Bản của tuyến khác không có bản cục bộ nên không sửa được. Thiếu cờ này thì
    # bác sĩ bấm Sửa rồi nhận lỗi mà không hiểu vì sao.
    assert theo_ma["N18.9"]["chi_doc"] is True


def test_khong_dem_doi_ban_da_lien_thong(his_va_truc):
    """E11.9 nằm ở cả bệnh án cục bộ lẫn trục - chỉ được hiện một dòng."""
    server, _ = his_va_truc
    p = _ho_so(server).json()[0]
    assert [c["icd10_code"] for c in p["conditions"]].count("E11.9") == 1


def test_ban_cua_vien_minh_tren_truc_khong_nhan_doi_ban_cuc_bo(make_his, monkeypatch):
    """
    Bản cục bộ CHƯA liên thông mà trục cũng có mã đó của chính viện này.

    Hai dòng "chưa liên thông" và "chỉ đọc" cho cùng một bệnh ở cùng một viện là
    hai điều không thể cùng đúng. Bệnh án cục bộ là bản gốc nên nó thắng.
    """
    truc = TrucGia(
        patients=[ho_so_tren_truc()],
        conditions=[cond("c-x", "J32.1", "Viêm xoang", "BV-A-001", "Bệnh viện A")])
    server = make_his(facility_code="BV-A-001", facility_name="Bệnh viện A")
    monkeypatch.setattr(server.requests, "get", truc.get)
    with server.db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO patients (id,name,gender,birth_date,clinical_note,"
                    "citizen_id,sync_status) VALUES (?,?,?,?,?,?,?)",
                    ("MRN-1", "Lê Văn Cường", "Nam", "1975-03-02", "xoang", CCCD, "Unsynced"))
        cur.execute("INSERT INTO patient_conditions (patient_id,icd10_code,icd10_display,"
                    "fhir_condition_id,status,clinical_status) VALUES (?,?,?,?,?,?)",
                    ("MRN-1", "J32.1", "Viêm xoang", None, "Unsynced", "active"))

    p = _ho_so(server).json()[0]
    assert [c["icd10_code"] for c in p["conditions"]] == ["J32.1"]
    assert p["conditions"][0]["chua_lien_thong"] is True


def test_chi_hai_lan_goi_truc_du_bao_nhieu_benh_nhan(make_his, monkeypatch):
    """
    Hỏi trục một lần cho mỗi bệnh nhân thì màn hình chậm dần theo số hồ sơ - kiểu
    chậm chỉ lộ ra khi dữ liệu đã nhiều, tức đúng lúc không sửa được nữa.
    """
    truc = TrucGia(patients=[], conditions=[])
    server = make_his(facility_code="BV-A-001", facility_name="Bệnh viện A")
    monkeypatch.setattr(server.requests, "get", truc.get)
    with server.db_cursor(commit=True) as cur:
        for i in range(8):
            cur.execute("INSERT INTO patients (id,name,gender,birth_date,clinical_note,"
                        "citizen_id,sync_status) VALUES (?,?,?,?,?,?,?)",
                        ("MRN-%d" % i, "BN %d" % i, "Nam", "1980-01-01", "x",
                         "0790950102%02d" % i, "Unsynced"))

    _ho_so(server)
    assert truc.so_lan_goi == 2, "Gọi trục %d lần cho 8 bệnh nhân" % truc.so_lan_goi


def test_truc_offline_van_hien_benh_an_cuc_bo(make_his, monkeypatch):
    """Trục hỏng không được làm hỏng luôn màn hình bệnh án của chính viện mình."""
    server = make_his(facility_code="BV-A-001", facility_name="Bệnh viện A")
    monkeypatch.setattr(server.requests, "get", TrucGia(offline=True).get)
    with server.db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO patients (id,name,gender,birth_date,clinical_note,"
                    "sync_status) VALUES (?,?,?,?,?,?)",
                    ("MRN-1", "Lê Văn Cường", "Nam", "1975-03-02", "đtđ", "Unsynced"))

    res = _ho_so(server)
    assert res.status_code == 200
    assert res.headers["X-EMR-Online"] == "false"
    assert res.json()[0]["id"] == "MRN-1"


def test_bao_trang_thai_truc_qua_header(his_va_truc):
    """
    Danh sách là mảng nên không nhét cờ trạng thái vào thân được.

    Không báo ra thì giao diện hiện một bệnh án trông có vẻ đầy đủ trong khi phần
    tuyến khác đang thiếu - im lặng sai còn tệ hơn báo lỗi.
    """
    server, _ = his_va_truc
    assert _ho_so(server).headers["X-EMR-Online"] == "true"
