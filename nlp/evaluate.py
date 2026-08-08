# -*- coding: utf-8 -*-
"""
Đánh giá định lượng mô hình chuẩn hóa chẩn đoán -> ICD-10.

Chỉ số báo cáo:
    Top-1 / Top-3 / Top-5   tỷ lệ mã đúng nằm trong k gợi ý đầu
    MRR                     Mean Reciprocal Rank, đo cả thứ hạng chứ không chỉ trúng/trượt
    Độ chính xác theo mức tin cậy   kiểm tra chính sách ngưỡng có hợp lý không

Cách chạy:
    .venv\\Scripts\\python -m nlp.evaluate
    .venv\\Scripts\\python -m nlp.evaluate --show-errors
    .venv\\Scripts\\python -m nlp.evaluate --json ket_qua.json
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.clinical_rules import CONFIDENCE_POLICY  # noqa: E402
from nlp.nlp_engine import NLPEngine, sanitize_icd10_code  # noqa: E402

EVAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "eval_set.json")
TOP_K = 5


def load_eval_set(path: str = EVAL_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def rank_of_hit(predictions, expected_codes) -> int:
    """Vị trí (1-based) của mã đúng đầu tiên trong danh sách gợi ý, 0 nếu trượt."""
    accepted = {sanitize_icd10_code(c).upper() for c in expected_codes}
    for position, pred in enumerate(predictions, start=1):
        if sanitize_icd10_code(pred["code"]).upper() in accepted:
            return position
    return 0


def evaluate(engine: NLPEngine, dataset: dict, top_k: int = TOP_K) -> dict:
    cases = dataset["cases"]
    per_tag = defaultdict(lambda: {"total": 0, "top1": 0, "top3": 0, "top5": 0})
    per_band = defaultdict(lambda: {"total": 0, "correct": 0})

    hits = {1: 0, 3: 0, 5: 0}
    reciprocal_sum = 0.0
    errors = []
    latencies = []

    for case in cases:
        started = time.perf_counter()
        predictions = engine.query(case["query"], top_k=top_k)
        latencies.append((time.perf_counter() - started) * 1000)

        rank = rank_of_hit(predictions, case["expected"])
        tag = case.get("tag", "plain")

        per_tag[tag]["total"] += 1
        for k in (1, 3, 5):
            if rank and rank <= k:
                hits[k] += 1
                per_tag[tag][f"top{k}"] += 1
        if rank:
            reciprocal_sum += 1.0 / rank

        if predictions:
            band = predictions[0]["confidence_band"]
            per_band[band]["total"] += 1
            if rank == 1:
                per_band[band]["correct"] += 1

        if rank != 1:
            errors.append({
                "query": case["query"],
                "expected": case["expected"],
                "tag": tag,
                "rank": rank,
                "got": [
                    {"code": p["code"], "name_vi": p["name_vi"], "confidence": p["confidence"]}
                    for p in predictions[:3]
                ],
            })

    total = len(cases)
    latencies.sort()
    return {
        "total_cases": total,
        "top1": hits[1] / total,
        "top3": hits[3] / total,
        "top5": hits[5] / total,
        "mrr": reciprocal_sum / total,
        "latency_ms": {
            "mean": sum(latencies) / len(latencies),
            "p50": latencies[len(latencies) // 2],
            "p95": latencies[int(len(latencies) * 0.95)],
        },
        "per_tag": {tag: dict(stats) for tag, stats in per_tag.items()},
        "per_band": {band: dict(stats) for band, stats in per_band.items()},
        "errors": errors,
    }


def validate_dataset(engine: NLPEngine, dataset: dict) -> list:
    """Cảnh báo các mã kỳ vọng không tồn tại trong danh mục - tránh test sai vì nhãn sai."""
    known = {sanitize_icd10_code(c).upper() for c in engine.db_index}
    missing = []
    for case in dataset["cases"]:
        for code in case["expected"]:
            if sanitize_icd10_code(code).upper() not in known:
                missing.append((case["query"], code))
    return missing


def print_report(result: dict, show_errors: bool = False):
    print("\n" + "=" * 72)
    print(f"KẾT QUẢ ĐÁNH GIÁ - {result['total_cases']} trường hợp lâm sàng")
    print("=" * 72)
    print(f"  Top-1 accuracy : {result['top1']:.1%}")
    print(f"  Top-3 accuracy : {result['top3']:.1%}")
    print(f"  Top-5 accuracy : {result['top5']:.1%}")
    print(f"  MRR            : {result['mrr']:.4f}")
    lat = result["latency_ms"]
    print(f"  Độ trễ         : trung bình {lat['mean']:.0f} ms | p50 {lat['p50']:.0f} ms | p95 {lat['p95']:.0f} ms")

    print("\n  Theo nhóm đặc thù đầu vào")
    print(f"  {'nhóm':<16}{'số ca':>7}{'Top-1':>9}{'Top-3':>9}{'Top-5':>9}")
    for tag, s in sorted(result["per_tag"].items(), key=lambda kv: -kv[1]["total"]):
        print(f"  {tag:<16}{s['total']:>7}{s['top1']/s['total']:>9.1%}"
              f"{s['top3']/s['total']:>9.1%}{s['top5']/s['total']:>9.1%}")

    print("\n  Hiệu chuẩn độ tin cậy (Top-1 đúng trong từng mức)")
    print(f"  {'mức':<10}{'số ca':>7}{'chính xác':>12}")
    for band in ("high", "medium", "low"):
        s = result["per_band"].get(band)
        if s and s["total"]:
            print(f"  {band:<10}{s['total']:>7}{s['correct']/s['total']:>12.1%}")
    print(f"  (ngưỡng tự động xác nhận: {CONFIDENCE_POLICY['auto_confirm']}%, "
          f"chờ duyệt: {CONFIDENCE_POLICY['provisional']}%)")

    if show_errors and result["errors"]:
        print(f"\n  {len(result['errors'])} trường hợp chưa đạt Top-1:")
        for err in result["errors"]:
            print(f"\n  [{err['tag']}] {err['query']}")
            print(f"     kỳ vọng: {', '.join(err['expected'])} | thứ hạng thực tế: "
                  f"{err['rank'] if err['rank'] else 'ngoài Top-5'}")
            for got in err["got"]:
                print(f"       - {got['code']:<9}{got['name_vi'][:48]:<50}{got['confidence']}%")
    print()


def main():
    parser = argparse.ArgumentParser(description="Đánh giá mô hình ánh xạ ICD-10")
    parser.add_argument("--show-errors", action="store_true", help="In chi tiết các ca sai")
    parser.add_argument("--json", metavar="FILE", help="Lưu kết quả ra file JSON")
    parser.add_argument("--dataset", default=EVAL_PATH, help="Đường dẫn bộ đánh giá")
    args = parser.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    dataset = load_eval_set(args.dataset)
    engine = NLPEngine()

    missing = validate_dataset(engine, dataset)
    if missing:
        print(f"\nCẢNH BÁO: {len(missing)} mã kỳ vọng không có trong danh mục ICD-10:")
        for query, code in missing[:15]:
            print(f"  - {code}  (ca: {query})")

    result = evaluate(engine, dataset)
    print_report(result, show_errors=args.show_errors)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Đã lưu kết quả chi tiết vào {args.json}")


if __name__ == "__main__":
    main()
