# -*- coding: utf-8 -*-
"""
Chẩn đoán lên trục phải mang TÊN BỆNH, không phải nhãn chỗ điền tạm.

VNPT HIS đẩy chẩn đoán kèm theo lên trục với `icd10_display` là đúng chuỗi
"Chẩn đoán kèm theo" - vì màn khám chỉ lưu MÃ của chẩn đoán kèm theo, tên bệnh
không đi cùng. Hậu quả đo được: I21.9 nằm trên trục dưới cái tên "Chẩn đoán kèm
theo", trong khi mã đó là **Nhồi máu cơ tim cấp**. Bác sĩ tuyến sau đọc bệnh án
về không biết bệnh nhân từng nhồi máu cơ tim.

Chữa ở Gateway chứ không ở HIS: Gateway là nơi duy nhất chắc chắn có đủ danh mục
12.137 mã, còn mỗi HIS chỉ mang theo một bảng nhỏ. Sửa một chỗ thì mọi HIS cùng
hưởng, kể cả HIS chưa viết.
"""
import pytest
from fastapi import HTTPException


class DanhMucGia:
    """Máy NLP tối giản: chỉ cần phần tra mã trong danh mục."""

    model_name = "danh-muc-gia"

    def __init__(self, muc=None):
        self.db_index = muc or {
            "I21.9": {"code": "I21.9", "code_no_dot": "I219",
                      "name_vi": "Nhồi máu cơ tim cấp, không đặc hiệu",
                      "name_en": "Acute myocardial infarction, unspecified"},
            "E87.7": {"code": "E87.7", "code_no_dot": "E877",
                      "name_vi": "Quá tải dịch"},
        }
        self.db = list(self.db_index.values())


@pytest.fixture
def gw(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "nlp_engine", DanhMucGia())
    return gateway


def _yeu_cau(gw, **kw):
    truong = dict(patient_id="MRN-1", patient_name="Lê Văn Cường",
                  raw_clinical_note="nhồi máu cơ tim", icd10_code="I21.9",
                  confidence_score=90.0)
    truong.update(kw)
    return gw.FHIRConvertRequest(**truong)


def _ten(resource):
    return resource["code"]["coding"][0]["display"]


def test_bo_trong_ten_thi_gateway_tra_trong_danh_muc(gw):
    """Đường đi mới của VNPT HIS: chỉ gửi mã, để Gateway đặt tên."""
    res = gw.convert_to_fhir(_yeu_cau(gw))
    assert _ten(res) == "Nhồi máu cơ tim cấp, không đặc hiệu"


def test_nhan_dien_tam_bi_bo_qua(gw):
    """
    Chốt đúng lỗi đã thấy trên trục.

    "Chẩn đoán kèm theo" nói rằng đây là bệnh phụ, KHÔNG nói đó là bệnh gì. Nhận
    nó làm tên bệnh là để một chẩn đoán vô nghĩa nằm trên trục.
    """
    for nhan in ("Chẩn đoán kèm theo", "chan doan kem theo", "  Chẩn đoán phụ  ",
                 "Chẩn đoán liên thông", "n/a", "-"):
        res = gw.convert_to_fhir(_yeu_cau(gw, icd10_display=nhan))
        assert _ten(res) == "Nhồi máu cơ tim cấp, không đặc hiệu", (
            f"Nhãn '{nhan}' lọt lên trục thành tên bệnh")


def test_danh_muc_thang_ten_ben_goi_gui_len(gw):
    """
    Danh mục là bản chính thức của Bộ Y tế, nên nó thắng.

    Theo đặc tả FHIR, `Coding.display` là cách diễn đạt nghĩa của mã TRONG hệ mã
    đó - không phải chỗ để mỗi HIS ghi cách gọi riêng. Để bên gọi thắng thì cùng
    một mã mang nhiều tên khác nhau trên trục tùy nơi ghi.
    """
    res = gw.convert_to_fhir(_yeu_cau(gw, icd10_display="Nhồi máu cơ tim"))
    assert _ten(res) == "Nhồi máu cơ tim cấp, không đặc hiệu"


def test_ma_ngoai_danh_muc_thi_dung_ten_ben_goi(gw):
    """
    Mã đúng định dạng nhưng danh mục còn thiếu - lúc đó có tên còn hơn không.

    Danh mục hiện thiếu tên cho 4.652 mã nhánh mở rộng, nên đây không phải trường
    hợp hiếm.
    """
    res = gw.convert_to_fhir(
        _yeu_cau(gw, icd10_code="Z99.8", icd10_display="Phụ thuộc thiết bị hỗ trợ khác"))
    assert _ten(res) == "Phụ thuộc thiết bị hỗ trợ khác"


def test_khong_danh_muc_khong_ten_thi_tu_choi(gw):
    """
    Không dựng Condition thiếu tên bệnh.

    Chẩn đoán chỉ có mã trần thì bác sĩ tuyến sau phải tự đi tra mới biết bệnh
    gì - mà nếu tra được thì Gateway đã tra được. Từ chối thẳng để lỗi lộ ra ở
    nơi ghi, thay vì nằm im trên trục tới lúc có người cần đọc.
    """
    with pytest.raises(HTTPException) as loi:
        gw.convert_to_fhir(_yeu_cau(gw, icd10_code="Z99.8"))
    assert loi.value.status_code == 422
    assert "tên bệnh" in loi.value.detail


def test_endpoint_tra_ten_cho_his_dung(gw):
    """HIS tra tên để hiện trên màn hình của chính nó, cùng một nguồn với trục."""
    assert gw.tra_ten_icd10("I219")["name_vi"] == "Nhồi máu cơ tim cấp, không đặc hiệu"
    assert gw.tra_ten_icd10("E87.7")["code_no_dot"] == "E877"

    with pytest.raises(HTTPException) as loi:
        gw.tra_ten_icd10("Z99.8")
    assert loi.value.status_code == 404
