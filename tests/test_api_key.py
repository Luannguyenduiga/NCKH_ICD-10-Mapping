# -*- coding: utf-8 -*-
"""
Khóa API gắn với cơ sở: danh tính của bên gọi phải là thứ đã xác thực (T1.4a).

Trước đây mã cơ sở là lời khai trong thân yêu cầu, cộng một cờ môi trường để
quyết định có tin lời khai hay không. Bộ này chốt ba điều:

1. Đường liên thông `/api/fhir/*` đòi khóa khi đã bật xác thực; khóa sai hoặc
   đã thu hồi bị 401 ở MỌI chế độ.
2. Cơ sở của một yêu cầu là cơ sở gắn với khóa. Khóa của viện A không ghi, không
   gỡ được hồ sơ mang mã viện B - kể cả khi Gateway bật cờ nhiều cơ sở.
3. Khối NLP không bị đụng tới: `/api/standardize` đi qua cửa xác thực mà không
   bị hỏi khóa, và bên gọi không mang khóa vẫn đi đúng đường cũ khi chưa bắt buộc.
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend import auth

CHUNG = dict(patient_name="Lê Thị K", raw_clinical_note="đtđ tuýp 2",
             icd10_code="E11.9", icd10_display="Đái tháo đường týp 2",
             confidence_score=93.0, encounter_date="2026-09-12")


@pytest.fixture
def key_store(tmp_path, monkeypatch):
    """Kho khóa tạm, trỏ module `auth` vào đó và trả về chế độ mặc định (auto)."""
    store_tmp = auth.KeyStore(str(tmp_path / "api_keys.json"))
    monkeypatch.setattr(auth, "key_store", store_tmp)
    monkeypatch.setattr(auth, "REQUIRE_API_KEY", "auto")
    return store_tmp


@pytest.fixture
def two_keys(key_store):
    """Một khóa cho mỗi bệnh viện demo. Mã BV-* phải qua cờ demo, như T1.1(b)."""
    key_a = key_store.issue("BV-A-001", "Bệnh viện Đa khoa A", "HIS A", allow_demo=True)
    key_b = key_store.issue("BV-B-002", "Bệnh viện Đa khoa B", "HIS B", allow_demo=True)
    return key_a, key_b


@pytest.fixture
def client(gateway):
    # Không chạy lifespan: không nạp mô hình NLP, không kiểm mã cơ sở lần nữa.
    return TestClient(gateway.app)


def _headers(key):
    return {auth.API_KEY_HEADER: key}


def _sinh(client, key=None, **them):
    return client.post("/api/fhir/condition", json={"patient_id": "BN-1", **CHUNG, **them},
                       headers=_headers(key) if key else {})


# --- Kho khóa --------------------------------------------------------------

def test_key_cap_ra_chi_luu_bam(key_store, tmp_path):
    key = key_store.issue("01001", "Bệnh viện A")
    tren_dia = (tmp_path / "api_keys.json").read_text(encoding="utf-8")

    assert key.startswith("smig_")
    assert key not in tren_dia, "Bản rõ của khóa không được nằm trên đĩa"
    assert json.loads(tren_dia)["keys"][0]["sha256"]
    assert key_store.lookup(key).facility_code == "01001"


def test_key_sai_mot_ky_tu_bi_tu_choi(key_store):
    key = key_store.issue("01001", "Bệnh viện A")
    assert key_store.lookup(key[:-1] + ("x" if key[-1] != "x" else "y")) is None
    assert key_store.lookup("smig_00000000_khong-ton-tai") is None
    assert key_store.lookup("") is None


def test_thu_hoi_co_hieu_luc_ngay(key_store):
    key = key_store.issue("01001", "Bệnh viện A")
    tien_to = auth.key_prefix_of(key)

    assert key_store.revoke(tien_to) is True
    assert key_store.lookup(key) is None
    assert key_store.active_count() == 0
    # Thu hồi lần hai không phải lỗi hệ thống, chỉ là "không còn gì để thu hồi".
    assert key_store.revoke(tien_to) is False


def test_cap_key_di_qua_cua_kiem_ma_co_so(key_store):
    """Khép lỗ hở 4.6 của T1.1: mã tự khai nay bị kiểm dạng lúc cấp khóa."""
    with pytest.raises(ValueError):
        key_store.issue("BV-A-001", "Bệnh viện A")          # mã tự đặt, chưa bật cờ demo
    with pytest.raises(ValueError):
        key_store.issue("0100", "Bệnh viện A")              # thiếu một ký tự
    with pytest.raises(ValueError):
        key_store.issue("01001", "   ")                     # thiếu tên
    assert key_store.issue("BV-A-001", "Bệnh viện A", allow_demo=True)


def test_kho_doc_lai_khi_tep_doi(key_store, tmp_path):
    """Thu hồi bằng CLI ở cửa sổ khác phải có tác dụng với Gateway đang chạy."""
    key = key_store.issue("01001", "Bệnh viện A")
    store_other = auth.KeyStore(str(tmp_path / "api_keys.json"))
    store_other.revoke(auth.key_prefix_of(key))
    assert key_store.lookup(key) is None


# --- Chế độ ----------------------------------------------------------------

def test_auto_bat_buoc_ngay_khi_co_key(key_store):
    assert auth.api_key_required() is False
    key_store.issue("01001", "Bệnh viện A")
    assert auth.api_key_required() is True


def test_bat_buoc_ma_kho_trong_thi_dung_luc_khoi_dong(key_store, monkeypatch):
    monkeypatch.setattr(auth, "REQUIRE_API_KEY", "1")
    with pytest.raises(RuntimeError) as loi:
        auth.check_api_key_config()
    assert "SMIG_REQUIRE_API_KEY" in str(loi.value)


def test_gia_tri_co_la_bi_tu_choi(key_store, monkeypatch):
    monkeypatch.setattr(auth, "REQUIRE_API_KEY", "co")
    with pytest.raises(RuntimeError):
        auth.check_api_key_config()


def test_tat_thi_khong_doi_key_du_kho_co_key(key_store, monkeypatch):
    key_store.issue("01001", "Bệnh viện A")
    monkeypatch.setattr(auth, "REQUIRE_API_KEY", "0")
    assert auth.api_key_required() is False


# --- Cửa xác thực trên HTTP -------------------------------------------------

def test_thieu_key_bi_401(client, two_keys):
    """Tiêu chí 10 của T1.md: gọi /api/fhir/sync thiếu khóa -> 401, không phải 200."""
    res = client.post("/api/fhir/sync", json={"resourceType": "Condition"})
    assert res.status_code == 401
    assert auth.API_KEY_HEADER in res.headers["WWW-Authenticate"]

    assert _sinh(client).status_code == 401
    assert client.get("/api/fhir/sync").status_code == 401
    assert client.delete("/api/fhir/sync").status_code == 401


def test_health_va_nlp_khong_bi_hoi_key(client, two_keys):
    """Khối NLP nằm ngoài phạm vi khóa; /health mở để giám sát."""
    assert client.get("/health").status_code == 200
    # Mô hình chưa nạp nên 503 - nhưng KHÔNG phải 401: cửa xác thực đã cho qua.
    res = client.post("/api/standardize", json={"query": "đtđ tuýp 2"})
    assert res.status_code == 503
    assert client.get("/api/icd10/E11.9").status_code == 503


def test_health_phoi_ra_che_do(client, two_keys):
    kq = client.get("/health").json()
    assert kq["require_api_key"] is True
    assert kq["api_keys_active"] == 2
    assert kq["api_key_header"] == auth.API_KEY_HEADER


def test_key_sai_bi_401_ke_ca_khi_khong_bat_buoc(client, key_store, monkeypatch):
    """Không bao giờ hạ một khóa sai xuống thành bên gọi nặc danh."""
    monkeypatch.setattr(auth, "REQUIRE_API_KEY", "0")
    assert _sinh(client, "smig_deadbeef_khong-co-that").status_code == 401


def test_key_da_thu_hoi_bi_401(client, key_store):
    key = key_store.issue("BV-A-001", "Bệnh viện Đa khoa A", allow_demo=True)
    assert _sinh(client, key).status_code == 200
    key_store.revoke(auth.key_prefix_of(key))
    assert _sinh(client, key).status_code == 401


# --- Cơ sở là cơ sở của khóa ------------------------------------------------

def test_co_so_lay_tu_key_khong_can_co_nhieu_co_so(client, gateway, two_keys):
    """
    Gateway cấu hình BV-A-001, cờ nhiều cơ sở TẮT. Khóa của viện B vẫn ghi được
    dưới tên viện B: danh tính đã xác thực thì không cần cờ nào cho phép nữa.
    """
    _, key_b = two_keys
    assert gateway.ALLOW_CLIENT_FACILITY is False

    res = _sinh(client, key_b)
    assert res.status_code == 200
    assert res.json()["meta"]["tag"][0]["code"] == "BV-B-002"
    # Cùng khóa, tự khai đúng mã của mình thì vẫn hợp lệ.
    assert _sinh(client, key_b, facility_code="BV-B-002").status_code == 200


def test_key_vien_a_khong_ghi_duoc_ma_vien_b(client, gateway, two_keys, monkeypatch):
    """Tiêu chí 11 của T1.md - và cờ nhiều cơ sở không mở được lối đi vòng."""
    key_a, _ = two_keys
    for co in (False, True):
        monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", co)
        res = _sinh(client, key_a, facility_code="BV-B-002", facility_name="Viện B")
        assert res.status_code == 403
        assert "BV-A-001" in res.json()["detail"]


def test_dong_bo_va_go_theo_dung_co_so_cua_key(client, emr, two_keys):
    key_a, key_b = two_keys
    condition = _sinh(client, key_b).json()
    kq = client.post("/api/fhir/sync", headers=_headers(key_b), json={
        "condition": condition, "patient": {"id": "BN-1", "name": "Lê Thị K"}})
    assert kq.status_code == 200, kq.text
    assert kq.json()["facility_code"] == "BV-B-002"
    assert list(emr.store["Organization"].values())[0]["identifier"][0]["value"] == "BV-B-002"

    cid = kq.json()["condition_id"]
    # Viện A cầm id của viện B: gỡ bị chặn, và không cần khai gì thêm để bị chặn.
    assert client.delete(f"/api/fhir/condition/{cid}", headers=_headers(key_a)).status_code == 403
    assert emr.facilities() == ["BV-B-002"]
    # Viện B gỡ bản của mình thì được.
    assert client.delete(f"/api/fhir/condition/{cid}", headers=_headers(key_b)).status_code == 200
    assert emr.facilities() == []


def test_dong_bo_tai_nguyen_mang_ma_la_bi_403(client, two_keys):
    """Đường /api/fhir/sync đọc mã cơ sở từ chính tài nguyên - vẫn phải khớp khóa."""
    key_a, key_b = two_keys
    cua_b = _sinh(client, key_b).json()
    res = client.post("/api/fhir/sync", headers=_headers(key_a), json={"condition": cua_b})
    assert res.status_code == 403


def test_xoa_hang_loat_chi_trong_pham_vi_key(client, emr, two_keys):
    key_a, key_b = two_keys
    for key, ma_ba in ((key_a, "BN-A"), (key_b, "BN-B")):
        cond = client.post("/api/fhir/condition", headers=_headers(key),
                           json={"patient_id": ma_ba, **CHUNG}).json()
        assert client.post("/api/fhir/sync", headers=_headers(key),
                           json={"condition": cond}).status_code == 200
    assert emr.facilities() == ["BV-A-001", "BV-B-002"]

    kq = client.delete("/api/fhir/sync", headers=_headers(key_a)).json()
    assert kq["deleted"] == 1
    assert emr.facilities() == ["BV-B-002"]
    # Khai `facility=BV-B-002` kèm khóa A cũng không được.
    assert client.delete("/api/fhir/sync?facility=BV-B-002",
                         headers=_headers(key_a)).status_code == 403


# --- Đường cũ vẫn nguyên khi chưa bắt buộc ---------------------------------

def test_khong_key_khi_chua_bat_buoc_di_dung_duong_cu(client, key_store, gateway, monkeypatch):
    monkeypatch.setattr(auth, "REQUIRE_API_KEY", "0")
    # Không khai gì: mã cấu hình của Gateway.
    assert _sinh(client).json()["meta"]["tag"][0]["code"] == "BV-A-001"
    # Khai mã lạ khi cờ tắt: 403 như trước, thông báo vẫn chỉ tới cờ.
    res = _sinh(client, facility_code="BV-B-002")
    assert res.status_code == 403
    assert "SMIG_ALLOW_CLIENT_FACILITY" in res.json()["detail"]
    # Bật cờ: lời khai được nhận, như trước T1.4.
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)
    assert _sinh(client, facility_code="BV-B-002").json()["meta"]["tag"][0]["code"] == "BV-B-002"


def test_danh_tinh_khong_ro_ri_sang_yeu_cau_sau(client, key_store, monkeypatch):
    """Yêu cầu có khóa B rồi yêu cầu nặc danh: yêu cầu sau phải về mã cấu hình."""
    monkeypatch.setattr(auth, "REQUIRE_API_KEY", "0")
    key_b = key_store.issue("BV-B-002", "Bệnh viện Đa khoa B", allow_demo=True)
    assert _sinh(client, key_b).json()["meta"]["tag"][0]["code"] == "BV-B-002"
    assert _sinh(client).json()["meta"]["tag"][0]["code"] == "BV-A-001"


def test_goi_thang_ham_khong_qua_http_khong_co_danh_tinh(gateway, two_keys):
    """Bộ test cũ gọi thẳng endpoint; đường đó không có bên gọi và hành vi y nguyên."""
    assert auth.current_caller() is None
    cond = gateway.convert_to_fhir(gateway.FHIRConvertRequest(patient_id="BN-1", **CHUNG))
    assert cond["meta"]["tag"][0]["code"] == "BV-A-001"
