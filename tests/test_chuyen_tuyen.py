# -*- coding: utf-8 -*-
"""
Kịch bản chuyển tuyến.

Bệnh viện A chẩn đoán đái tháo đường, không chữa được nên chuyển bệnh nhân lên
bệnh viện B. B khám ra thêm bệnh thận mạn. Ba câu hỏi cần trả lời được:

    1. B thêm được bệnh mới mà KHÔNG đè lên chẩn đoán của A?
    2. Hai nơi quy về cùng một người nhờ chung số CCCD?
    3. Bác sĩ ở B tra được bệnh sử bằng CCCD - thứ duy nhất họ có trong tay khi
       tiếp nhận một bệnh nhân chưa từng khám ở đây?
"""
import pytest

CCCD = "079075001234"


@pytest.fixture
def da_chuyen_tuyen(gateway, emr, monkeypatch):
    """Dựng sẵn trạng thái sau khi cả hai bệnh viện đã liên thông."""
    monkeypatch.setattr(gateway, "ALLOW_CLIENT_FACILITY", True)

    def lien_thong(ma_co_so, ten_co_so, ma_benh_an, ma_icd, ten_benh, mo_ta):
        condition = gateway.convert_to_fhir(gateway.FHIRConvertRequest(
            patient_id=ma_benh_an, patient_name="Lê Văn Cường",
            raw_clinical_note=mo_ta, icd10_code=ma_icd, icd10_display=ten_benh,
            confidence_score=93.0, citizen_id=CCCD,
            facility_code=ma_co_so, facility_name=ten_co_so))
        return gateway.sync_fhir_record({"condition": condition, "patient": {
            "id": ma_benh_an, "name": "Lê Văn Cường", "gender": "Nam",
            "birth_date": "1975-03-02", "citizen_id": CCCD}})

    a = lien_thong("BV-A-001", "Bệnh viện Đa khoa A", "BN-A-100",
                   "E11.9", "Đái tháo đường týp 2", "đtđ tuýp 2 không kiểm soát được")
    b = lien_thong("BV-B-002", "Bệnh viện Đa khoa B", "BN-B-77",
                   "N18.9", "Bệnh thận mạn", "suy thận mạn")
    return emr, a, b


def test_vien_b_them_benh_khong_de_len_chan_doan_cua_a(da_chuyen_tuyen):
    emr, _, _ = da_chuyen_tuyen
    assert emr.codes() == ["E11.9", "N18.9"]
    assert emr.facilities() == ["BV-A-001", "BV-B-002"]


def test_chung_cccd_thi_quy_ve_mot_benh_nhan(da_chuyen_tuyen):
    emr, a, b = da_chuyen_tuyen
    assert len(emr.store["Patient"]) == 1
    assert a["patient_id"] == b["patient_id"]


def test_bac_si_vien_b_tra_benh_su_bang_cccd(da_chuyen_tuyen, make_his):
    """
    Mã bệnh án chỉ có nghĩa trong nội bộ một viện, nên với bệnh nhân chuyển tuyến
    nó luôn trượt. Chỉ tra theo mã bệnh án là ca chuyển tuyến không bao giờ ra
    bệnh sử, đúng vào lúc cần bệnh sử nhất.
    """
    emr, _, _ = da_chuyen_tuyen
    his = make_his("BV-B-002", "Bệnh viện Đa khoa B", emr_client=emr)

    ket_qua = his.get_patients(search_id=CCCD)
    assert ket_qua, "Tra bằng CCCD không ra hồ sơ nào"
    ho_so = ket_qua[0]

    # Tra bằng CCCD thì cột mã bệnh án phải hiện MÃ BỆNH ÁN, không phải số CCCD.
    assert ho_so["id"] == "BN-B-77"
    assert ho_so["citizen_id"] == CCCD
    assert ho_so["is_local"] is False

    ma = sorted(c["icd10_code"] for c in ho_so["conditions"])
    assert ma == ["E11.9", "N18.9"]

    ngoai_vien = [c for c in ho_so["conditions"] if c["la_ngoai_vien"]]
    assert [c["icd10_code"] for c in ngoai_vien] == ["E11.9"]
    assert ngoai_vien[0]["facility_name"] == "Bệnh viện Đa khoa A"


def test_tra_bang_ma_benh_an_cua_chinh_vien_do_van_chay(da_chuyen_tuyen, make_his):
    emr, _, _ = da_chuyen_tuyen
    his = make_his("BV-B-002", "Bệnh viện Đa khoa B", emr_client=emr)
    assert his.get_patients(search_id="BN-B-77")[0]["id"] == "BN-B-77"


def test_ma_benh_an_cua_vien_khac_khong_tra_ra(da_chuyen_tuyen, make_his):
    """
    "BN-A-100" là mã do viện A cấp, chỉ có nghĩa trong phạm vi viện A. Viện B tra
    theo mã đó phải KHÔNG ra gì - ra được nghĩa là hai viện đang dùng chung không
    gian mã bệnh án, và hai bệnh nhân trùng mã sẽ bị trộn.
    """
    emr, _, _ = da_chuyen_tuyen
    his = make_his("BV-B-002", "Bệnh viện Đa khoa B", emr_client=emr)
    assert his.get_patients(search_id="BN-A-100") == []
