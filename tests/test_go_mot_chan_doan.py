# -*- coding: utf-8 -*-
"""
Gỡ MỘT chẩn đoán cũng phải dừng trong phạm vi cơ sở của mình.

`test_xoa_theo_co_so.py` đã chốt đường xóa HÀNG LOẠT. Đường gỡ MỘT bản ghi thì
trước đây nhận thẳng `condition_id` rồi xóa, không hỏi bản ghi đó của ai - trong
khi `GET /api/fhir/sync` cố ý trả chẩn đoán của MỌI cơ sở kèm `id`, vì nhìn thấy
hồ sơ nơi khác lập chính là điều cần trình bày.

Ghép hai đường lại là xóa được chéo: đọc danh sách, lấy id của bệnh viện B, gọi
gỡ. Bộ test này chốt đúng chỗ đó, và chốt luôn rằng vá xong thì luồng sửa chẩn
đoán của chính mình vẫn chạy bình thường.
"""
import pytest
from fastapi import HTTPException

CHUNG = dict(patient_name="Vũ Văn E", raw_clinical_note="đtđ tuýp 2",
             icd10_display="Đái tháo đường týp 2",
             confidence_score=93.0, encounter_date="2026-08-15")


@pytest.fixture
def hai_vien(gateway, emr, monkeypatch):
    """Trên trục có sẵn chẩn đoán của cả hai bệnh viện, kèm id do EMR cấp."""
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)

    def lien_thong(ma_co_so, ten_co_so, ma_benh_an, ma_icd):
        condition = gateway.convert_to_fhir(gateway.FHIRConvertRequest(
            patient_id=ma_benh_an, facility_code=ma_co_so, facility_name=ten_co_so,
            icd10_code=ma_icd, **CHUNG))
        ket_qua = gateway.sync_fhir_record({"condition": condition, "patient": {
            "id": ma_benh_an, "name": "Vũ Văn E"}})
        return ket_qua["condition_id"]

    id_a = lien_thong("BV-A-001", "Bệnh viện Đa khoa A", "BN-A-1", "E11.9")
    id_b = lien_thong("BV-B-002", "Bệnh viện Đa khoa B", "BN-B-1", "N18.9")

    # Gateway trở về đúng cấu hình mặc định (chỉ phục vụ BV-A-001) để bài test
    # mô phỏng kẻ gọi bình thường, không phải chế độ nhiều cơ sở.
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", False)
    return gateway, emr, id_a, id_b


def test_khong_go_duoc_chan_doan_do_vien_khac_lap(hai_vien):
    """Lỗ hổng chính: biết id của bệnh viện B thì trước đây xóa được."""
    gateway, emr, _, id_b = hai_vien

    with pytest.raises(HTTPException) as loi:
        gateway.delete_synced_condition(id_b)

    assert loi.value.status_code == 403
    assert "BV-B-002" in loi.value.detail
    assert emr.facilities() == ["BV-A-001", "BV-B-002"], "Đã gỡ mất bản ghi của viện khác"


def test_id_lay_tu_danh_sach_chung_van_khong_go_duoc(hai_vien):
    """
    Chốt đúng đường tấn công, không chỉ chốt hàm.

    Đi qua `GET /api/fhir/sync` như một bên gọi thật: danh sách này cố ý không
    lọc theo cơ sở, nên id của bệnh viện B nằm sẵn trong tay bên gọi.
    """
    gateway, emr, _, _ = hai_vien

    tren_truc = gateway.get_synced_records(count=10)
    id_cua_b = [c["id"] for c in tren_truc
                if c["meta"]["tag"][0]["code"] == "BV-B-002"]
    assert id_cua_b, "Danh sách chung phải thấy được chẩn đoán của viện khác"

    for cid in id_cua_b:
        with pytest.raises(HTTPException) as loi:
            gateway.delete_synced_condition(cid)
        assert loi.value.status_code == 403

    assert emr.codes() == ["E11.9", "N18.9"]


def test_van_go_duoc_chan_doan_cua_chinh_minh(hai_vien):
    """Vá lỗ không được chặn luôn luồng bác sĩ sửa lại chẩn đoán của mình."""
    gateway, emr, id_a, _ = hai_vien

    ket_qua = gateway.delete_synced_condition(id_a)

    assert ket_qua["status"] == "success"
    assert ket_qua["facility_code"] == "BV-A-001"
    assert emr.facilities() == ["BV-B-002"]


def test_go_lai_ban_ghi_da_mat_van_bao_thanh_cong(hai_vien):
    """
    Giữ tính lặp lại được: HIS gọi lại sau khi mất mạng thì không được báo lỗi.

    `hospital_his.server._retire_on_emr` dựa vào điều này - nó cố ý không ném
    ngoại lệ, nên một lỗi giả ở đây sẽ hiện lên giao diện bác sĩ như bản ghi thừa
    còn sót trên trục.
    """
    gateway, _, id_a, _ = hai_vien
    gateway.delete_synced_condition(id_a)

    lan_hai = gateway.delete_synced_condition(id_a)
    assert lan_hai["status"] == "success"
    assert "đã không còn" in lan_hai["message"]


def test_khai_ma_co_so_khac_theo_dung_chinh_sach(hai_vien, monkeypatch):
    """Cùng một luật với đường ghi và đường xóa hàng loạt, không có ngoại lệ."""
    gateway, emr, _, id_b = hai_vien

    # Chưa bật cờ nhiều cơ sở: khai mã lạ bị chặn ngay ở resolve_facility.
    with pytest.raises(HTTPException) as loi:
        gateway.delete_synced_condition(id_b, facility="BV-B-002")
    assert loi.value.status_code == 403
    assert emr.facilities() == ["BV-A-001", "BV-B-002"]

    # Bật lên thì bệnh viện B tự gỡ bản ghi của chính mình được.
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)
    assert gateway.delete_synced_condition(id_b, facility="BV-B-002")["status"] == "success"
    assert emr.facilities() == ["BV-A-001"]


def test_ban_ghi_cu_khong_mang_the_co_so_van_go_duoc(hai_vien):
    """
    Dữ liệu có từ trước khi Gateway gắn thẻ cơ sở thì không quy được về ai.

    Cho gỡ - không có cơ sở nào để bảo vệ, mà từ chối thì chúng kẹt trên trục
    vĩnh viễn. Đây là đánh đổi có chủ ý, nên chốt lại để ai đổi hành vi này phải
    đổi cả bài test và đọc được lý do.
    """
    gateway, emr, id_a, _ = hai_vien
    emr.store["Condition"][id_a].pop("meta", None)

    assert gateway.delete_synced_condition(id_a)["status"] == "success"
    assert id_a not in emr.store["Condition"]
