# -*- coding: utf-8 -*-
"""
Kiểm thử tự động cho bộ xử lý NLP.

Nguyên tắc: mọi ca kiểm thử phải chỉ đích danh mã ICD-10 đúng.

Phiên bản trước dùng tiền tố lỏng ("E1" khớp cả E10 lẫn E11, "I1" khớp cả I10 lẫn
I15) nên bộ test báo 4/4 PASS trong khi mô hình đang trả về E10.3 cho "đái tháo
đường tuýp 2" (sai típ bệnh) và I15 cho "cao huyết áp vô căn" (sai nguyên phát/
thứ phát). Một bộ test xanh mà che giấu lỗi lâm sàng còn nguy hiểm hơn test đỏ.

Cách chạy:
    .venv\\Scripts\\python -m nlp.test_nlp        (chạy trực tiếp)
    .venv\\Scripts\\python -m pytest nlp/test_nlp.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.evaluate import evaluate, load_eval_set  # noqa: E402
from nlp.nlp_engine import NLPEngine, sanitize_icd10_code  # noqa: E402

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ngưỡng hồi quy trên tập phát triển. Đặt sát mức hiện tại để mọi thay đổi làm
# giảm chất lượng đều bị chặn ngay.
MIN_DEV_TOP1 = 0.95
MIN_DEV_TOP3 = 0.98
# Ngưỡng trên tập kiểm tra độc lập - thấp hơn vì đây mới là năng lực tổng quát hóa thật.
MIN_HOLDOUT_TOP1 = 0.65
MIN_HOLDOUT_TOP5 = 0.88


@pytest.fixture(scope="module")
def engine():
    return NLPEngine()


# --- Các ca lâm sàng cốt lõi: mã phải đúng chính xác, không chấp nhận tiền tố lỏng ---
CORE_CASES = [
    ("Bệnh nhân bị ĐTĐ typ 2", {"E11", "E11.9"},
     "Giải nghĩa viết tắt + phân biệt típ 2 (E11) với típ 1 (E10)"),
    ("cao huyết áp vô căn", {"I10"},
     "Phân biệt tăng huyết áp nguyên phát (I10) với thứ phát (I15)"),
    ("Theo dõi NMCT cấp", {"I21", "I21.9"},
     "Viết tắt NMCT + bóc tiền tố 'theo dõi'"),
    ("hen phe quan cap", {"J45", "J45.9", "J46"},
     "Khôi phục dấu tiếng Việt từ văn bản gõ không dấu"),
    ("đái tháo đường tuýp 1", {"E10", "E10.9"},
     "Chiều ngược lại của trục típ bệnh"),
    ("tăng huyết áp thứ phát", {"I15", "I15.9"},
     "Chiều ngược lại của trục nguyên phát/thứ phát"),
]


@pytest.mark.parametrize("query,accepted,description", CORE_CASES)
def test_core_clinical_cases(engine, query, accepted, description):
    """Mã Top-1 phải nằm đúng trong tập mã chấp nhận được."""
    results = engine.query(query)
    assert results, f"Không trả về kết quả nào cho '{query}'"

    top = sanitize_icd10_code(results[0]["code"])
    accepted_clean = {sanitize_icd10_code(c) for c in accepted}
    assert top in accepted_clean, (
        f"{description}\n  Truy vấn : {query}\n"
        f"  Kỳ vọng  : {sorted(accepted_clean)}\n"
        f"  Nhận được: {top} - {results[0]['name_vi']} ({results[0]['confidence']}%)"
    )


def test_dagger_stripped_from_codes(engine):
    """Mã trả ra không được chứa ký hiệu dao găm - máy chủ FHIR sẽ từ chối."""
    results = engine.query("đái tháo đường tuýp 2 có biến chứng thận", top_k=5)
    for r in results:
        assert "†" not in r["code"], f"Mã {r['code']} còn ký hiệu dao găm"


def test_abbreviation_expansion(engine):
    """Viết tắt phải được giải nghĩa trước khi mã hóa embedding."""
    expanded = engine.expand_query("BN bị ĐTĐ typ 2")
    assert "đái tháo đường" in expanded
    assert "tuýp 2" in expanded
    # Cầu nối từ vựng: danh mục ICD-10 dùng "không phụ thuộc insuline", không dùng "tuýp 2".
    assert "không phụ thuộc insuline" in expanded


def test_diacritic_restoration(engine):
    """Văn bản gõ không dấu phải được khôi phục dấu trước khi truy hồi."""
    assert "nhồi máu cơ tim" in engine.expand_query("nhoi mau co tim cap")
    # Văn bản đã có dấu đúng thì không bị đụng tới.
    assert "nhồi máu cơ tim cấp" in engine.expand_query("nhồi máu cơ tim cấp")


def test_noise_prefix_stripping_is_iterative(engine):
    """Các tiền tố hành văn xếp chồng phải được bóc hết, không chỉ một lượt."""
    expanded = engine.expand_query("Hiện tại bệnh nhân được chẩn đoán: cao huyết áp vô căn")
    assert not expanded.startswith("hiện tại")
    assert not expanded.startswith("bệnh nhân")
    assert "tăng huyết áp" in expanded


def test_entity_extraction(engine):
    """NER theo luật phải bắt được thực thể và gắn đúng mã."""
    entities = engine.extract_entities_regex("Bệnh nhân ĐTĐ typ 2 kèm cao huyết áp")
    codes = {e["code"] for e in entities}
    assert any(c.startswith("E11") for c in codes), f"Thiếu thực thể đái tháo đường: {entities}"
    assert "I10" in codes, f"Thiếu thực thể tăng huyết áp: {entities}"


def test_confidence_is_discriminative(engine):
    """
    Độ tin cậy phải phân biệt được ca chắc chắn và ca mơ hồ.

    Nếu mọi kết quả đều 100% thì chính sách ngưỡng duyệt của bác sĩ mất tác dụng.
    """
    confident = engine.query("tăng huyết áp vô căn")[0]["confidence"]
    vague = engine.query("bệnh nhân mệt mỏi khó chịu trong người")[0]["confidence"]
    assert confident > vague, f"Ca rõ ràng ({confident}%) phải tự tin hơn ca mơ hồ ({vague}%)"
    assert confident >= 85, f"Ca chuẩn phải đạt mức tự động xác nhận, đang là {confident}%"


def test_dev_set_regression(engine):
    """Chặn hồi quy trên tập phát triển."""
    result = evaluate(engine, load_eval_set())
    assert result["top1"] >= MIN_DEV_TOP1, (
        f"Top-1 tập phát triển tụt còn {result['top1']:.1%} (ngưỡng {MIN_DEV_TOP1:.0%})")
    assert result["top3"] >= MIN_DEV_TOP3, (
        f"Top-3 tập phát triển tụt còn {result['top3']:.1%} (ngưỡng {MIN_DEV_TOP3:.0%})")


def test_holdout_generalization(engine):
    """
    Chặn hồi quy trên tập kiểm tra độc lập.

    Đây là chỉ số quan trọng nhất: tập phát triển đã được dùng để hiệu chỉnh luật
    nên kết quả 100% trên đó KHÔNG phản ánh năng lực tổng quát hóa.
    """
    holdout_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "eval_holdout.json")
    result = evaluate(engine, load_eval_set(holdout_path))
    assert result["top1"] >= MIN_HOLDOUT_TOP1, (
        f"Top-1 tập held-out tụt còn {result['top1']:.1%} (ngưỡng {MIN_HOLDOUT_TOP1:.0%})")
    assert result["top5"] >= MIN_HOLDOUT_TOP5, (
        f"Top-5 tập held-out tụt còn {result['top5']:.1%} (ngưỡng {MIN_HOLDOUT_TOP5:.0%})")


def _run_standalone() -> int:
    """Chạy không cần pytest, in báo cáo dễ đọc cho buổi trình bày."""
    print("Khởi tạo NLP Engine...")
    eng = NLPEngine()

    print("\n--- Các ca lâm sàng cốt lõi (yêu cầu mã chính xác) ---")
    passed = 0
    for query, accepted, description in CORE_CASES:
        results = eng.query(query)
        accepted_clean = {sanitize_icd10_code(c) for c in accepted}
        ok = bool(results) and sanitize_icd10_code(results[0]["code"]) in accepted_clean

        print(f"\n{'[PASS]' if ok else '[FAIL]'} {query}")
        print(f"        {description}")
        print(f"        chuẩn hóa: {eng.expand_query(query)}")
        if results:
            top = results[0]
            print(f"        kết quả  : {top['code']} - {top['name_vi']} "
                  f"({top['confidence']}%, {top['confidence_band']})")
        if not ok:
            print(f"        kỳ vọng  : {sorted(accepted_clean)}")
        passed += ok

    print(f"\nCác ca cốt lõi: {passed}/{len(CORE_CASES)}")

    holdout_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "eval_holdout.json")
    dev = evaluate(eng, load_eval_set())
    hold = evaluate(eng, load_eval_set(holdout_path))

    print("\n--- Chỉ số định lượng ---")
    print(f"  Tập phát triển ({dev['total_cases']} ca, ĐÃ dùng để hiệu chỉnh luật):")
    print(f"      Top-1 {dev['top1']:.1%} | Top-3 {dev['top3']:.1%} | MRR {dev['mrr']:.3f}")
    print(f"  Tập kiểm tra độc lập ({hold['total_cases']} ca, phản ánh năng lực thật):")
    print(f"      Top-1 {hold['top1']:.1%} | Top-5 {hold['top5']:.1%} | MRR {hold['mrr']:.3f}")

    ok = (passed == len(CORE_CASES)
          and dev["top1"] >= MIN_DEV_TOP1
          and hold["top1"] >= MIN_HOLDOUT_TOP1
          and hold["top5"] >= MIN_HOLDOUT_TOP5)
    print(f"\nKẾT LUẬN: {'ĐẠT' if ok else 'KHÔNG ĐẠT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(_run_standalone())
    except Exception as exc:  # noqa: BLE001
        print(f"\nLỗi trong quá trình kiểm thử: {exc}")
        sys.exit(1)
