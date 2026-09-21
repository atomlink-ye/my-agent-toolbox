#!/usr/bin/env python3
"""Apply the Phase 1 or Phase 2 retrieval quality acceptance gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


TOLERANCE = 1e-12
METRICS = ("recall_at_5", "recall_at_10", "mrr_at_10")
CONTROL_CATEGORIES = {"identifier", "lexical-control"}


def at_least(actual: float, minimum: float) -> bool:
    return actual + TOLERANCE >= minimum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("1", "2"), required=True)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    before_document = json.loads(args.before.read_text(encoding="utf-8"))
    after_document = json.loads(args.after.read_text(encoding="utf-8"))
    before, after = before_document.get("baseline", before_document), after_document.get("baseline", after_document)
    failures: list[dict] = []
    query_differences: list[dict] = []

    def require(label: str, actual: float, minimum: float) -> None:
        if not at_least(actual, minimum):
            failures.append({"rule": label, "actual": actual, "minimum": minimum})

    before_rows = {row["id"]: row for row in before["results"]}
    after_rows = {row["id"]: row for row in after["results"]}
    for query_id, old in before_rows.items():
        new = after_rows.get(query_id)
        if new is None:
            failures.append({"rule": "query_missing", "query": query_id})
            continue
        if new.get("category") != old.get("category") or new.get("query") != old.get("query") or new.get("expected_path") != old.get("expected_path"):
            failures.append({"rule": "query_definition_changed", "query": query_id})
            continue
        old_rank, new_rank = old.get("rank"), new.get("rank")
        if old_rank != new_rank:
            query_differences.append({"query": query_id, "category": old["category"], "before_rank": old_rank, "after_rank": new_rank})
        for window in (5, 10):
            if old_rank is not None and old_rank <= window and (new_rank is None or new_rank > window):
                failures.append({"rule": f"recall_at_{window}_dropout", "query": query_id, "category": old["category"], "before_rank": old_rank, "after_rank": new_rank})
        if old["category"] in CONTROL_CATEGORIES and (new_rank is None or old_rank is None or new_rank > old_rank):
            failures.append({"rule": "control_rank_regression", "query": query_id, "category": old["category"], "before_rank": old_rank, "after_rank": new_rank})

    for query_id in sorted(set(after_rows) - set(before_rows)):
        failures.append({"rule": "unexpected_query", "query": query_id})

    bm, am = before["category_metrics"], after["category_metrics"]
    if args.phase == "1":
        floors = {
            "cjk": tuple(bm["cjk"][m] for m in METRICS),
            "identifier": (1.0, 1.0, 1.0),
            "lexical-control": (1.0, 1.0, bm["lexical-control"]["mrr_at_10"]),
            "long-natural-language": (0.75, 0.75, 0.35),
            "mixed-language": tuple(bm["mixed-language"][m] for m in METRICS),
            "morphology-punctuation": tuple(bm["morphology-punctuation"][m] for m in METRICS),
            "paraphrase": tuple(bm["paraphrase"][m] for m in METRICS),
            "ranking": tuple(bm["ranking"][m] for m in METRICS),
        }
        overall = (before["metrics"]["recall_at_5"], before["metrics"]["recall_at_10"], 0.720)
    else:
        floors = {category: tuple(bm[category][metric] for metric in METRICS) for category in bm}
        floors.update({
            "cjk": tuple(max(value, floor) for value, floor in zip(floors["cjk"], (2 / 3, 1.0, 0.5))),
            "identifier": (1.0, 1.0, 1.0),
            "lexical-control": (1.0, 1.0, bm["lexical-control"]["mrr_at_10"]),
            "mixed-language": tuple(max(value, floor) for value, floor in zip(floors["mixed-language"], (2 / 3, 1.0, 2 / 3))),
        })
        overall = tuple(max(before["metrics"][metric], floor) for metric, floor in zip(METRICS, (0.85, 0.925, 0.760)))

    for category, minimums in floors.items():
        if category not in am:
            failures.append({"rule": "category_missing", "category": category})
            continue
        for metric, minimum in zip(METRICS, minimums):
            require(f"{category}.{metric}", am[category][metric], minimum)
    for metric, minimum in zip(METRICS, overall):
        require(f"overall.{metric}", after["metrics"][metric], minimum)

    budget = {"assessed": False}
    if args.phase == "2" and "costs" in before_document and "costs" in after_document:
        before_costs, after_costs = before_document["costs"], after_document["costs"]
        budget = {"assessed": True, "limits": {}, "actual": {}}
        storage_limit = before_costs["storage_bytes"] * 1.5
        budget["limits"]["storage_bytes"] = storage_limit
        budget["actual"]["storage_bytes"] = after_costs["storage_bytes"]
        if after_costs["storage_bytes"] > storage_limit:
            failures.append({"rule": "budget.storage_bytes", "actual": after_costs["storage_bytes"], "maximum": storage_limit})
        sync_limit = before_costs["sync_median_seconds"] * 2.0
        budget["limits"]["sync_median_seconds"] = sync_limit
        budget["actual"]["sync_median_seconds"] = after_costs["sync_median_seconds"]
        if after_costs["sync_median_seconds"] > sync_limit + TOLERANCE:
            failures.append({"rule": "budget.sync_median_seconds", "actual": after_costs["sync_median_seconds"], "maximum": sync_limit})
        for group in ("overall", "ascii", "han"):
            old_p95 = before_costs["search_latency"][group]["p95_seconds"]
            new_p95 = after_costs["search_latency"][group]["p95_seconds"]
            limit = max(2 * old_p95, old_p95 + 0.010)
            budget["limits"][f"{group}_p95_seconds"] = limit
            budget["actual"][f"{group}_p95_seconds"] = new_p95
            if new_p95 > limit + TOLERANCE:
                failures.append({"rule": f"budget.{group}_p95_seconds", "actual": new_p95, "maximum": limit})

    report = {
        "status": "PASS" if not failures else "FAIL", "phase": int(args.phase),
        "tolerance": TOLERANCE, "query_differences": query_differences,
        "budget": budget, "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
