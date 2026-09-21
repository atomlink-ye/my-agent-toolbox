#!/usr/bin/env python3
"""Rebuild an isolated Agent Memory index and score retrieval judgments.

This harness intentionally imports the production search implementation.  It uses
only the Python standard library and writes the disposable SQLite database beneath
a temporary directory (or --work-dir), never beneath ~/.agent-memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "skills" / "agent-memory" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from memory_config import collect_memory_roots  # noqa: E402
from memory_store_ext import connect_db, search_documents, sync_index  # noqa: E402


def load_judgments(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) < 30:
        raise ValueError("judgments must be a JSON array with at least 30 entries")
    required = {"id", "category", "query", "expected_path", "rationale"}
    for index, item in enumerate(data, 1):
        missing = required - set(item)
        if missing:
            raise ValueError(f"judgment {index} is missing: {sorted(missing)}")
    return data


def relpath(path: str, corpus: Path) -> str:
    return Path(path).resolve().relative_to(corpus.resolve()).as_posix()


def corpus_digest(corpus: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(corpus.rglob("*.md")):
        digest.update(path.relative_to(corpus).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def metrics(ranks: list[int | None]) -> dict[str, float]:
    count = len(ranks)
    return {
        "recall_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / count,
        "recall_at_10": sum(rank is not None and rank <= 10 for rank in ranks) / count,
        "mrr_at_10": statistics.fmean(
            1.0 / rank if rank is not None and rank <= 10 else 0.0 for rank in ranks
        ),
    }


def run(corpus: Path, judgments_path: Path, work_dir: Path) -> dict:
    judgments = load_judgments(judgments_path)
    expected = {item["expected_path"] for item in judgments}
    missing = sorted(path for path in expected if not (corpus / path).is_file())
    if missing:
        raise FileNotFoundError(f"expected corpus documents do not exist: {missing}")

    settings_path = work_dir / "settings.json"
    settings = {
        "version": 1,
        "database": "index.sqlite3",
        "shared": [],
        "bindings": [
            {"path": str((corpus / "arcp").resolve()), "project": "arcp", "memory": "."},
            {
                "path": str((corpus / "agent-server").resolve()),
                "project": "agent-server",
                "memory": ".",
            },
        ],
    }
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    db_path = work_dir / "index.sqlite3"
    conn = connect_db(db_path)
    sync = sync_index(conn, collect_memory_roots(settings, settings_path))
    rows = []
    for item in judgments:
        results = search_documents(conn, item["query"], limit=10)
        paths = [relpath(result["path"], corpus) for result in results]
        try:
            rank = paths.index(item["expected_path"]) + 1
        except ValueError:
            rank = None
        rows.append(
            {
                "id": item["id"],
                "category": item["category"],
                "query": item["query"],
                "expected_path": item["expected_path"],
                "rank": rank,
                "top_paths": paths,
            }
        )
    conn.close()
    count = len(judgments)
    category_metrics = {}
    for category in sorted({row["category"] for row in rows}):
        category_metrics[category] = metrics(
            [row["rank"] for row in rows if row["category"] == category]
        )
    return {
        "corpus": str(corpus.resolve()),
        "corpus_sha256": corpus_digest(corpus),
        "database": str(db_path.resolve()),
        "settings": str(settings_path.resolve()),
        "sync": sync,
        "judgments": count,
        "category_counts": dict(sorted(Counter(x["category"] for x in judgments).items())),
        "metrics": metrics([row["rank"] for row in rows]),
        "category_metrics": category_metrics,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("/home/agent/am/amcorpus"))
    parser.add_argument(
        "--judgments",
        type=Path,
        default=Path(__file__).with_name("judgments.json"),
    )
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        result = run(args.corpus, args.judgments, args.work_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="agent-memory-retrieval-") as tmp:
            result = run(args.corpus, args.judgments, Path(tmp))
            # Do not advertise a database path that disappears after this block.
            result["database"] = "temporary (deleted after run)"
            result["settings"] = "temporary (deleted after run)"
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
