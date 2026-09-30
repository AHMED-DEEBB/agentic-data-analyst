"""Evaluation suite: measures the agent the way a production team would.

Metrics
  execution_accuracy  answer cases where the agent's result matches the gold query's result
  refusal_accuracy    unsafe / out-of-scope cases the agent correctly refused
  overall             all cases passed / all cases
  self_correction     answer cases that needed at least one SQL repair
  p50/p95 latency

Matching (standard "execution accuracy", tolerant of harmless differences):
  - same number of rows
  - every gold column matches SOME predicted column (extra columns and different column
    names or order are fine); numbers match within 0.5% or 0.01; text is case-insensitive
  - date/month label columns are not compared (their format varies: '2025-01' vs date)
  - a pivoted answer is accepted: gold "one row per group" vs predicted "one row, one
    column per group" with the same numbers (e.g. online_revenue, store_revenue)

Usage
  python -m evals.run_evals                     # full suite
  python -m evals.run_evals --limit 15          # quick subset (CI)
  python -m evals.run_evals --min-overall 0.8   # exit 1 below threshold (quality gate)
  python -m evals.run_evals --ids ar11,ar12     # rerun specific cases

Infrastructure errors (rate limits, timeouts) are reported as ERROR and excluded from the
accuracy scores, because they say nothing about the agent's quality. They are counted in
the summary so nothing is hidden.
"""

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

import yaml

from app.db import run_readonly

HERE = Path(__file__).parent
DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?")


def _norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        return s.lower()


def _is_date_column(values) -> bool:
    return all(isinstance(v, str) and DATE_RE.match(v) for v in values if v is not None)


def _columns_equal(a: list, b: list) -> bool:
    if len(a) != len(b):
        return False
    a_sorted = sorted(a, key=lambda x: (x is None, isinstance(x, str), x if x is not None else 0))
    b_sorted = sorted(b, key=lambda x: (x is None, isinstance(x, str), x if x is not None else 0))
    for x, y in zip(a_sorted, b_sorted, strict=True):
        if isinstance(x, float) and isinstance(y, float):
            if abs(x - y) > max(0.01, 0.005 * abs(x)):
                return False
        elif x != y:
            return False
    return True


def _pivot_match(gold_rows: list[list], pred_rows: list[list]) -> bool:
    """Gold has N rows with one numeric column; prediction is one row holding the same N numbers."""
    if len(pred_rows) != 1 or len(gold_rows) < 2:
        return False
    gold_numeric = [
        [_norm(v) for v in col] for col in zip(*gold_rows, strict=True)
        if all(isinstance(_norm(v), float) for v in col) and not _is_date_column(col)
    ]
    pred_numeric = [v for v in (_norm(x) for x in pred_rows[0]) if isinstance(v, float)]
    return len(gold_numeric) == 1 and _columns_equal(gold_numeric[0], pred_numeric)


def results_match(gold_rows: list[list], pred_cols: list[str], pred_rows: list[list]) -> bool:
    if _pivot_match(gold_rows, pred_rows):
        return True
    if len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        return True
    gold_columns = list(zip(*gold_rows, strict=True))
    pred_columns = [[_norm(v) for v in col] for col in zip(*pred_rows, strict=True)] if pred_rows else []
    for gcol in gold_columns:
        if _is_date_column(gcol):
            continue
        g = [_norm(v) for v in gcol]
        if not any(_columns_equal(g, p) for p in pred_columns):
            return False
    return True


def run_case(case: dict, ask) -> dict:
    started = time.perf_counter()
    try:
        result = ask(case["question"])
    except Exception as e:  # keep going; infra errors are tracked separately from failures
        text = f"{type(e).__name__}: {e}"
        infra = any(k in text for k in ("RateLimitError", "429", "APITimeoutError", "APIConnectionError"))
        return {"id": case["id"], "lang": case["lang"], "kind": case.get("expect", "answer"),
                "passed": False, "infra_error": infra, "error": text[:300],
                "latency_ms": int((time.perf_counter() - started) * 1000), "attempts": 0}

    kind = case.get("expect", "answer")
    if kind == "refuse":
        passed = result["status"] == "refused"
    else:
        _, gold_rows = run_readonly(case["gold_sql"])
        passed = result["status"] == "ok" and results_match(gold_rows, result["columns"], result["rows"])
    return {"id": case["id"], "lang": case["lang"], "kind": kind, "passed": passed,
            "status": result["status"], "sql": result.get("sql"), "attempts": result.get("attempts", 0),
            "latency_ms": result["latency_ms"]}


def summarize(all_rows: list[dict]) -> dict:
    rows = [r for r in all_rows if not r.get("infra_error")]

    def rate(items):
        return round(sum(r["passed"] for r in items) / len(items), 3) if items else None

    answers = [r for r in rows if r["kind"] == "answer"]
    latencies = sorted(r["latency_ms"] for r in rows)
    return {
        "cases": len(all_rows),
        "scored": len(rows),
        "infra_errors": len(all_rows) - len(rows),
        "overall": rate(rows),
        "execution_accuracy": rate(answers),
        "execution_accuracy_en": rate([r for r in answers if r["lang"] == "en"]),
        "execution_accuracy_ar": rate([r for r in answers if r["lang"] == "ar"]),
        "refusal_accuracy": rate([r for r in rows if r["kind"] == "refuse"]),
        "self_correction_rate": rate([{"passed": r["attempts"] > 0} for r in answers]),
        "latency_p50_ms": int(statistics.median(latencies)) if latencies else None,
        "latency_p95_ms": latencies[int(0.95 * (len(latencies) - 1))] if latencies else None,
    }


def write_report(summary: dict, rows: list[dict]) -> None:
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "report.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=2, ensure_ascii=False), encoding="utf-8")

    def pct(v):
        return "n/a" if v is None else f"{v * 100:.1f}%"

    lines = [
        "# Evaluation report", "",
        "| Metric | Value |", "|---|---|",
        f"| Cases | {summary['cases']} (scored {summary['scored']}, infra errors {summary['infra_errors']}) |",
        f"| Overall | {pct(summary['overall'])} |",
        f"| Execution accuracy | {pct(summary['execution_accuracy'])} |",
        f"| Execution accuracy (English) | {pct(summary['execution_accuracy_en'])} |",
        f"| Execution accuracy (Arabic) | {pct(summary['execution_accuracy_ar'])} |",
        f"| Refusal accuracy | {pct(summary['refusal_accuracy'])} |",
        f"| Needed self-correction | {pct(summary['self_correction_rate'])} |",
        f"| Latency p50 / p95 | {summary['latency_p50_ms']} ms / {summary['latency_p95_ms']} ms |",
        "", "## Failed cases", "",
    ]
    failed = [r for r in rows if not r["passed"] and not r.get("infra_error")]
    lines += [f"- `{r['id']}` status={r.get('status')} {r.get('error', '')}" for r in failed] or ["None"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Run a stratified subset of N cases")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds between cases (free-tier rate limits)")
    parser.add_argument("--min-overall", type=float, default=None, help="Fail (exit 1) below this score")
    parser.add_argument("--ids", type=str, default=None, help="Comma-separated case ids to run")
    args = parser.parse_args()

    from app.agents.runner import ask

    cases = yaml.safe_load((HERE / "dataset.yaml").read_text(encoding="utf-8"))["cases"]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:  # every k-th case keeps the EN / AR / refusal mix
        step = max(1, len(cases) // args.limit)
        cases = cases[::step][: args.limit]

    rows = []
    for i, case in enumerate(cases, 1):
        row = run_case(case, ask)
        rows.append(row)
        label = "PASS" if row["passed"] else ("ERROR (rate limit / infra)" if row.get("infra_error") else "FAIL")
        print(f"[{i}/{len(cases)}] {label} {case['id']} ({row['latency_ms']} ms)")
        if args.delay:
            time.sleep(args.delay)

    summary = summarize(rows)
    write_report(summary, rows)
    print(json.dumps(summary, indent=2))

    if summary["infra_errors"]:
        print(f"Note: {summary['infra_errors']} case(s) hit infrastructure errors and were not scored. Rerun them with --ids.")
    if args.min_overall is not None and (summary["overall"] or 0) < args.min_overall:
        print(f"Quality gate failed: overall {summary['overall']} < {args.min_overall}")
        sys.exit(1)


if __name__ == "__main__":
    main()