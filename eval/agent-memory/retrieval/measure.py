#!/usr/bin/env python3
"""Measure the frozen retrieval benchmark and its production costs."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

import benchmark

from memory_config import collect_memory_roots  # noqa: E402
from memory_store_ext import connect_db, search_documents, sync_index  # noqa: E402


EXPECTED_CORPUS_SHA256 = "150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c"
EXPECTED_INDEXED = 523
HAN_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))
HAN_DIAGNOSTICS = ("沙箱", "数据面可用")  # 2-char and 3+-char real-corpus probes


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    """Return the nearest-rank percentile (p95 of 20 samples is sample 19)."""
    if not values:
        raise ValueError("cannot calculate a percentile of an empty sample")
    ordered = sorted(values)
    return ordered[max(0, (len(ordered) * int(fraction * 100) + 99) // 100 - 1)]


def has_han(text: str) -> bool:
    return any(lo <= ord(char) <= hi for char in text for lo, hi in HAN_RANGES)


def settings(corpus: Path) -> dict:
    return {
        "version": 1,
        "database": "index.sqlite3",
        "shared": [],
        "bindings": [
            {"path": str((corpus / "arcp").resolve()), "project": "arcp", "memory": "."},
            {"path": str((corpus / "agent-server").resolve()), "project": "agent-server", "memory": "."},
        ],
    }


def rebuild(corpus: Path, directory: Path) -> dict:
    directory.mkdir(parents=True)
    settings_path = directory / "settings.json"
    config = settings(corpus)
    settings_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    db_path = directory / "index.sqlite3"
    conn = connect_db(db_path)
    started = time.perf_counter_ns()
    sync = sync_index(conn, collect_memory_roots(config, settings_path))
    elapsed = (time.perf_counter_ns() - started) / 1_000_000_000
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    conn.close()
    paths = {name: Path(f"{db_path}{suffix}") for name, suffix in (("database", ""), ("wal", "-wal"), ("shm", "-shm"))}
    sizes = {name: path.stat().st_size if path.exists() else 0 for name, path in paths.items()}
    return {"seconds": elapsed, "sync": sync, "sizes_bytes": sizes, "persistent_bytes": sum(sizes.values()), "database": str(db_path)}


def latency(db_path: Path, judgments: list[dict], rounds: int) -> dict:
    conn = connect_db(db_path)
    for item in judgments:  # exactly one fixed warmup round
        search_documents(conn, item["query"], limit=10)
    samples = {"overall": [], "ascii": [], "han": []}
    for _ in range(rounds):
        for item in judgments:
            started = time.perf_counter_ns()
            search_documents(conn, item["query"], limit=10)
            elapsed = (time.perf_counter_ns() - started) / 1_000_000_000
            samples["overall"].append(elapsed)
            samples["han" if has_han(item["query"]) else "ascii"].append(elapsed)
    conn.close()
    return {
        group: {"samples": len(values), "p50_seconds": percentile(values, 0.50), "p95_seconds": percentile(values, 0.95)}
        for group, values in samples.items()
    }


def han_diagnostics(db_path: Path, corpus: Path) -> list[dict]:
    conn = connect_db(db_path)
    output = []
    for query in HAN_DIAGNOSTICS:
        evidence = []
        for path in sorted(corpus.rglob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            offset = text.find(query)
            if offset >= 0:
                evidence.append({
                    "path": path.relative_to(corpus).as_posix(),
                    "excerpt": text[max(0, offset - 30):offset + len(query) + 30].replace("\n", " "),
                })
        hits = search_documents(conn, query, limit=10)
        output.append({
            "query": query, "han_characters": len(query), "corpus_evidence": evidence,
            "result_paths": [Path(hit["path"]).resolve().relative_to(corpus.resolve()).as_posix() for hit in hits],
        })
    conn.close()
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("/home/agent/am/amcorpus"))
    parser.add_argument("--judgments", type=Path, default=Path(__file__).with_name("judgments.json"))
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rebuilds", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args()
    if args.rebuilds < 1 or args.rounds < 1:
        parser.error("--rebuilds and --rounds must be positive")

    owned_tmp = args.work_dir is None
    root = args.work_dir or Path(tempfile.mkdtemp(prefix="agent-memory-measure-"))
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"work directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    try:
        corpus_hash = benchmark.corpus_digest(args.corpus)
        judgments = benchmark.load_judgments(args.judgments)
        builds = [rebuild(args.corpus, root / f"rebuild-{index + 1}") for index in range(args.rebuilds)]
        benchmark_dir = root / "benchmark"
        benchmark_dir.mkdir()
        baseline = benchmark.run(args.corpus, args.judgments, benchmark_dir)
        sync_valid = all(item["sync"].get("indexed") == EXPECTED_INDEXED for item in builds) and baseline["sync"].get("indexed") == EXPECTED_INDEXED
        input_valid = corpus_hash == EXPECTED_CORPUS_SHA256 and sync_valid
        result = {
            "input_valid": input_valid,
            "expected": {"corpus_sha256": EXPECTED_CORPUS_SHA256, "indexed": EXPECTED_INDEXED},
            "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version},
            "inputs": {
                "corpus": str(args.corpus.resolve()), "corpus_sha256": corpus_hash,
                "benchmark_sha256": sha256(Path(__file__).with_name("benchmark.py")),
                "judgments_sha256": sha256(args.judgments),
            },
            "baseline": baseline,
            "costs": {
                "sync_seconds": [item["seconds"] for item in builds],
                "sync_median_seconds": statistics.median(item["seconds"] for item in builds),
                "storage_runs": [{"sizes_bytes": item["sizes_bytes"], "persistent_bytes": item["persistent_bytes"]} for item in builds],
                "storage_bytes": builds[-1]["persistent_bytes"],
                "search_latency": latency(Path(builds[-1]["database"]), judgments, args.rounds),
                "han_diagnostics": han_diagnostics(Path(builds[-1]["database"]), args.corpus),
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if input_valid else 1
    finally:
        if owned_tmp:
            shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
