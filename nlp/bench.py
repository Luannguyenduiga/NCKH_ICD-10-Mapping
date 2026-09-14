# -*- coding: utf-8 -*-
"""
Bộ đo lường dùng chung cho các thử nghiệm cải tiến khối NLP.

Mở rộng `evaluate.py` (vốn chỉ đo Top-k/MRR/độ trễ trên MỘT tập) với những thứ
cần để QUYẾT ĐỊNH một thay đổi có nên giữ hay không:

    1. Ca theo dõi đích danh (watchlist) - biết chính xác 2 ca lỗi đã ghi trong
       docs/thaydoi.md có hết sai không, và 2 ca đối chứng có bị phá không.
       Số tổng (Top-1 trung bình) có thể đứng yên trong khi một ca cụ thể vừa
       hết sai vừa có ca khác mới sai bù vào - watchlist bắt được điều đó.
    2. Khoảng tin cậy Wilson cho Top-1 - eval_holdout chỉ 51 ca, chênh 1-2 ca
       đã đổi vài điểm phần trăm, nên một con số điểm không đủ để kết luận.
    3. Expected Calibration Error (ECE) - đo hiệu chuẩn định lượng, mịn hơn 3
       dải confidence_band hiện có.
    4. Compute: độ trễ và bộ nhớ, tách riêng phần phát sinh thêm bởi mỗi kỹ
       thuật (vd. NLI) khỏi baseline.
    5. So sánh hai lần chạy (baseline vs candidate) thành một báo cáo, để mỗi
       vòng thử nghiệm đều được đánh giá bằng đúng một bộ tiêu chí cố định.

Cách dùng:
    .venv\\Scripts\\python -m nlp.bench --save runs\\baseline.json
    .venv\\Scripts\\python -m nlp.bench --compare runs\\baseline.json
    .venv\\Scripts\\python -m nlp.bench --compare runs\\baseline.json --use-nli
"""

import argparse
import json
import math
import os
import statistics
import sys
import time
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.evaluate import EVAL_PATH, evaluate, load_eval_set, validate_dataset  # noqa: E402
from nlp.nlp_engine import NLPEngine, sanitize_icd10_code  # noqa: E402

HOLDOUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "eval_holdout.json")

# ---------------------------------------------------------------------------
# 1. Ca theo dõi đích danh
# ---------------------------------------------------------------------------
# Nguồn: docs/thaydoi.md mục 4.1. Hai ca đầu là lỗi đã biết (mã ".9 không đặc
# hiệu" thắng oan dù câu có nêu thể bệnh). Hai ca sau là đối chứng bắt buộc
# phải giữ nguyên đúng - chúng dùng CHUNG một khối mã, chỉ khác việc câu có
# nêu thể bệnh hay không, nên là bài kiểm định trực tiếp cho việc sửa
# UNSPECIFIED_PENALTY có tổng quát hóa đúng hay chỉ vá được đúng 2 ca đã biết.
WATCHLIST = [
    {"query": "viêm kết mạc dị ứng", "expect_top1": "H10.1",
     "must_not": "H10.9", "note": "lỗi đã biết: câu nêu 'dị ứng' vẫn ra .9"},
    {"query": "viem phoi thuy", "expect_top1": "J18.1",
     "must_not": "J18.9", "note": "lỗi đã biết: câu nêu 'thuỳ' vẫn ra .9"},
    {"query": "viêm phổi", "expect_top1": "J18.9",
     "must_not": None, "note": "đối chứng: không nêu thể -> .9 PHẢI đúng"},
    {"query": "viêm kết mạc", "expect_top1": "H10.9",
     "must_not": None, "note": "đối chứng: không nêu thể -> .9 PHẢI đúng"},
]


def run_watchlist(engine: NLPEngine) -> List[dict]:
    results = []
    for item in WATCHLIST:
        preds = engine.query(item["query"], top_k=3)
        top1 = sanitize_icd10_code(preds[0]["code"]).upper() if preds else None
        expect = sanitize_icd10_code(item["expect_top1"]).upper()
        ok = top1 == expect
        results.append({
            "query": item["query"],
            "expect": expect,
            "got": top1,
            "confidence": preds[0]["confidence"] if preds else None,
            "pass": ok,
            "note": item["note"],
        })
    return results


# ---------------------------------------------------------------------------
# 2. Khoảng tin cậy Wilson
# ---------------------------------------------------------------------------
def wilson_ci(successes: int, n: int, z: float = 1.96) -> Dict[str, float]:
    """Khoảng tin cậy Wilson cho một tỉ lệ - ổn định hơn khoảng chuẩn khi n nhỏ."""
    if n == 0:
        return {"low": 0.0, "high": 0.0}
    p = successes / n
    denom = 1 + z ** 2 / n
    centre = p + z ** 2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    return {"low": max(0.0, (centre - margin) / denom),
            "high": min(1.0, (centre + margin) / denom)}


# ---------------------------------------------------------------------------
# 3. Expected Calibration Error
# ---------------------------------------------------------------------------
def expected_calibration_error(cases: List[dict], engine: NLPEngine, n_bins: int = 10) -> Dict:
    """
    ECE trên dự đoán Top-1: chia theo dải confidence, so độ chính xác thật của
    từng dải với confidence trung bình dải đó. Hiệu chuẩn tốt thì hai số này
    gần nhau ở MỌI dải, không chỉ đúng tổng thể.
    """
    bins = [[] for _ in range(n_bins)]
    for case in cases:
        preds = engine.query(case["query"], top_k=1)
        if not preds:
            continue
        conf = preds[0]["confidence"] / 100.0
        correct = sanitize_icd10_code(preds[0]["code"]).upper() in {
            sanitize_icd10_code(c).upper() for c in case["expected"]}
        idx = min(n_bins - 1, int(conf * n_bins))
        bins[idx].append((conf, correct))

    total = sum(len(b) for b in bins)
    ece, detail = 0.0, []
    for i, b in enumerate(bins):
        if not b:
            continue
        avg_conf = sum(c for c, _ in b) / len(b)
        acc = sum(1 for _, ok in b if ok) / len(b)
        weight = len(b) / total
        ece += weight * abs(avg_conf - acc)
        detail.append({"bin": f"{i/n_bins:.1f}-{(i+1)/n_bins:.1f}", "n": len(b),
                        "avg_confidence": round(avg_conf, 4), "accuracy": round(acc, 4)})
    return {"ece": round(ece, 4), "bins": detail}


# ---------------------------------------------------------------------------
# 4. Compute: độ trễ và bộ nhớ
# ---------------------------------------------------------------------------
def measure_compute(engine: NLPEngine, sample_queries: List[str], n_reps: int = 3) -> Dict:
    import torch

    # Khởi động: lần gọi đầu luôn chậm hơn (nạp kernel CUDA, cache lười).
    engine.query(sample_queries[0])

    latencies = []
    for _ in range(n_reps):
        for q in sample_queries:
            started = time.perf_counter()
            engine.query(q)
            latencies.append((time.perf_counter() - started) * 1000)
    latencies.sort()

    mem = {}
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        mem = {
            "gpu_allocated_mb": round(torch.cuda.memory_allocated() / 1024 ** 2, 1),
            "gpu_reserved_mb": round(torch.cuda.memory_reserved() / 1024 ** 2, 1),
        }

    n = len(latencies)
    return {
        "n_queries": n,
        "mean_ms": round(sum(latencies) / n, 2),
        "p50_ms": round(latencies[n // 2], 2),
        "p95_ms": round(latencies[int(n * 0.95)], 2),
        "max_ms": round(latencies[-1], 2),
        **mem,
    }


# ---------------------------------------------------------------------------
# 5. Chạy đầy đủ một cấu hình
# ---------------------------------------------------------------------------
def run_full(engine: NLPEngine, label: str = "") -> Dict:
    dev = load_eval_set(EVAL_PATH)
    holdout = load_eval_set(HOLDOUT_PATH)

    dev_result = evaluate(engine, dev)
    holdout_result = evaluate(engine, holdout)
    watchlist = run_watchlist(engine)
    ece = expected_calibration_error(holdout["cases"], engine)

    sample = [c["query"] for c in holdout["cases"][:15]]
    compute = measure_compute(engine, sample)

    n = holdout_result["total_cases"]
    ci = wilson_ci(round(holdout_result["top1"] * n), n)

    return {
        "label": label,
        "dev": {"top1": dev_result["top1"], "top3": dev_result["top3"],
                "top5": dev_result["top5"], "mrr": dev_result["mrr"],
                "per_tag": dev_result["per_tag"]},
        "holdout": {"top1": holdout_result["top1"], "top3": holdout_result["top3"],
                    "top5": holdout_result["top5"], "mrr": holdout_result["mrr"],
                    "top1_ci95": ci, "per_tag": holdout_result["per_tag"],
                    "per_band": holdout_result["per_band"]},
        "ece_holdout": ece["ece"],
        "watchlist": watchlist,
        "compute": compute,
    }


# ---------------------------------------------------------------------------
# 6. So sánh hai lần chạy
# ---------------------------------------------------------------------------
def compare(baseline: Dict, candidate: Dict) -> Dict:
    dev_regressions = [
        tag for tag, s in candidate["dev"]["per_tag"].items()
        if s["top1"] < baseline["dev"]["per_tag"].get(tag, {}).get("top1", 0)
    ]
    watch_before = {w["query"]: w["pass"] for w in baseline["watchlist"]}
    watch_after = {w["query"]: w["pass"] for w in candidate["watchlist"]}
    newly_fixed = [q for q, ok in watch_after.items() if ok and not watch_before.get(q)]
    newly_broken = [q for q, ok in watch_after.items() if not ok and watch_before.get(q)]

    no_dev_regression = not dev_regressions
    holdout_top1_ok = candidate["holdout"]["top1"] >= baseline["holdout"]["top1"]
    watchlist_ok = not newly_broken
    improved = no_dev_regression and holdout_top1_ok and watchlist_ok and bool(newly_fixed or
        candidate["holdout"]["top1"] > baseline["holdout"]["top1"])

    latency_delta_ms = candidate["compute"]["mean_ms"] - baseline["compute"]["mean_ms"]

    return {
        "improved": improved,
        "reasons": {
            "no_dev_regression": no_dev_regression,
            "dev_regressions": dev_regressions,
            "holdout_top1_not_worse": holdout_top1_ok,
            "watchlist_no_new_break": watchlist_ok,
            "newly_fixed": newly_fixed,
            "newly_broken": newly_broken,
        },
        "deltas": {
            "dev_top1": round(candidate["dev"]["top1"] - baseline["dev"]["top1"], 4),
            "holdout_top1": round(candidate["holdout"]["top1"] - baseline["holdout"]["top1"], 4),
            "holdout_mrr": round(candidate["holdout"]["mrr"] - baseline["holdout"]["mrr"], 4),
            "ece": round(candidate["ece_holdout"] - baseline["ece_holdout"], 4),
            "latency_ms": round(latency_delta_ms, 2),
        },
    }


def print_report(result: Dict, diff: Optional[Dict] = None):
    print("\n" + "=" * 78)
    print(f"BÁO CÁO ĐO LƯỜNG - {result.get('label', '')}")
    print("=" * 78)
    d, h = result["dev"], result["holdout"]
    print(f"  eval_set  (n=110): Top-1 {d['top1']:.1%}  Top-3 {d['top3']:.1%}  "
          f"Top-5 {d['top5']:.1%}  MRR {d['mrr']:.4f}")
    print(f"  holdout   (n=51) : Top-1 {h['top1']:.1%} "
          f"[95% CI {h['top1_ci95']['low']:.1%}-{h['top1_ci95']['high']:.1%}]  "
          f"Top-3 {h['top3']:.1%}  Top-5 {h['top5']:.1%}  MRR {h['mrr']:.4f}")
    print(f"  ECE (holdout)    : {result['ece_holdout']:.4f}  (càng gần 0 càng hiệu chuẩn tốt)")
    c = result["compute"]
    print(f"  Độ trễ           : mean {c['mean_ms']} ms | p50 {c['p50_ms']} ms | p95 {c['p95_ms']} ms")
    if "gpu_allocated_mb" in c:
        print(f"  GPU memory       : allocated {c['gpu_allocated_mb']} MB | reserved {c['gpu_reserved_mb']} MB")

    print("\n  Watchlist:")
    for w in result["watchlist"]:
        mark = "PASS" if w["pass"] else "FAIL"
        print(f"    [{mark}] {w['query']:<28} kỳ vọng {w['expect']:<8} "
              f"nhận {str(w['got']):<8} ({w['confidence']}%)  - {w['note']}")

    if diff:
        print("\n  So với baseline:")
        print(f"    Cải thiện: {'CÓ' if diff['improved'] else 'CHƯA'}")
        for k, v in diff["deltas"].items():
            print(f"    delta {k:<14}: {v:+}")
        if diff["reasons"]["dev_regressions"]:
            print(f"    !! Hồi quy trên eval_set ở tag: {diff['reasons']['dev_regressions']}")
        if diff["reasons"]["newly_broken"]:
            print(f"    !! Watchlist mới hỏng: {diff['reasons']['newly_broken']}")
        if diff["reasons"]["newly_fixed"]:
            print(f"    Watchlist mới sửa được: {diff['reasons']['newly_fixed']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Đo lường và so sánh các thử nghiệm cải tiến NLP")
    parser.add_argument("--label", default="")
    parser.add_argument("--save", metavar="FILE", help="Lưu kết quả ra file JSON")
    parser.add_argument("--compare", metavar="FILE", help="So sánh với một file baseline đã lưu")
    parser.add_argument("--no-nli", action="store_true",
                        help="Tắt tín hiệu NLI (NLPEngine bật NLI mặc định) - dùng để đo baseline sạch")
    parser.add_argument("--nli-weight", type=float, default=0.20)
    parser.add_argument("--nli-top-k", type=int, default=4)
    parser.add_argument("--nli-template", default="bare", choices=["bare", "patient", "diagnosis"])
    parser.add_argument("--nli-floor", type=float, default=0.5, help="Ngưỡng contradiction tối thiểu để trừ điểm")
    args = parser.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if args.no_nli:
        kwargs = {"use_nli": False}
    else:
        kwargs = dict(use_nli=True, nli_weight=args.nli_weight, nli_top_k=args.nli_top_k,
                      nli_template=args.nli_template, nli_contradiction_floor=args.nli_floor)
    engine = NLPEngine(**kwargs)

    result = run_full(engine, label=args.label or ("baseline" if args.no_nli else "nli"))

    diff = None
    if args.compare:
        with open(args.compare, "r", encoding="utf-8") as f:
            baseline = json.load(f)
        diff = compare(baseline, result)

    print_report(result, diff)

    if args.save:
        os.makedirs(os.path.dirname(args.save) or ".", exist_ok=True)
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Đã lưu kết quả vào {args.save}")

    if diff is not None:
        sys.exit(0 if diff["improved"] else 2)


if __name__ == "__main__":
    main()
