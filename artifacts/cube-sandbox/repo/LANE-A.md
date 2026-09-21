# Lane A — retrieval and derived index report

## Delivered

- Phase 1 keeps the original word-FTS AND-first path intact. After an empty AND
  result, ordinary natural-language queries use ordered term de-duplication, a
  small explicit lowercase stopword set, and equal-weight FTS5 BM25 instead of
  the previous content-only Python substring count. Route diagnostics now come
  from the actual FTS queries.
- Phase 2 adds a versioned ordinary FTS5 `document_cjk_fts(grams)` derived index.
  It contains stable de-duplicated bigrams from contiguous Han runs only, with no
  cross-field/run grams and no unigram or trigram route.
- Main and auxiliary rows are inserted, replaced, and deleted in the same sync
  transaction. A separate `derived_indexes` marker enables the CJK route only
  after a successful current-version sync. Missing or stale state degrades to the
  word route without read-time DDL.
- Han queries always evaluate word and CJK routes. Full-run validation follows
  gram AND; the required partial-coverage fallback follows gram OR. Equal-weight
  RRF (`k=60`) fuses route ranks, de-duplicates document IDs, and uses path as the
  stable tie-breaker. Both routes apply the same scope/tag/shared filters before
  candidate truncation.
- Results retain numeric `score` and add actual-route `match_mode`,
  `matched_terms`, `missing_terms`, `retrieval_routes`; fused results also expose
  `rank_score`, and CJK results expose `matched_grams` and `gram_coverage`.

## Red baseline and quality evidence

Frozen inputs were checked out from `1c000df` only for local evaluation and were
not staged. Baseline command:

```sh
AM_RUN_DIR=$(mktemp -d /tmp/agent-memory-phase0.XXXXXX)
PYTHONDONTWRITEBYTECODE=1 python3 eval/agent-memory/retrieval/benchmark.py \
  --corpus /home/agent/am/amcorpus --work-dir "$AM_RUN_DIR" \
  --output "$AM_RUN_DIR/metrics.json"
```

Exit `0`; corpus SHA
`150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c`,
`indexed=523`. Baseline grouped values were:

| group | Recall@5 | Recall@10 | MRR@10 |
|---|---:|---:|---:|
| overall | 0.800000 | 0.825000 | 0.703333 |
| CJK | 0 | 0 | 0 |
| identifier | 1 | 1 | 1 |
| lexical-control | 1 | 1 | 0.958333 |
| long-natural-language | 0.750000 | 0.750000 | 0.175000 |
| mixed-language | 0.666667 | 0.666667 | 0.666667 |

After Phase 1, the same command exited `0`: long-natural-language became
`0.750000 / 1.000000 / 0.416667`; identifier remained `1/1/1`, lexical-control
remained `1/1/0.958333`, mixed-language remained
`0.666667/0.666667/0.666667`, and overall became
`0.800000/0.850000/0.727500`.

Final Phase 2 command was the same benchmark with a fresh work directory. Exit
`0`, `indexed=523`, corpus SHA unchanged:

| group | Recall@5 | Recall@10 | MRR@10 |
|---|---:|---:|---:|
| overall | 0.900000 | 0.950000 | 0.826458 |
| CJK | 1 | 1 | 1 |
| identifier | 1 | 1 | 1 |
| lexical-control | 1 | 1 | 0.958333 |
| long-natural-language | 0.750000 | 1 | 0.406250 |
| mixed-language | 1 | 1 | 1 |
| morphology-punctuation | 0.750000 | 1 | 0.775000 |
| paraphrase | 0.333333 | 0.333333 | 0.333333 |
| ranking | 1 | 1 | 0.766667 |

All six identifier judgments remained rank 1. Lexical-control ranks remained
`1,1,1,2,1,1,1,1,1,1,1,1`.

## Regression, consistency, and filter evidence

```sh
set -o pipefail
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s eval/agent-memory/tests -p 'test_*.py' 2>&1 | tee /tmp/lane-a-final-tests.log | tail -3
```

Exit `0`:

```text
Ran 37 tests in 2.999s

OK
```

A temporary one-document Han root was synced, updated, deleted, restored, and
synced twice. Observed `(main rows, auxiliary rows, query hits)` were:

```text
initial        1 1 ['note.md']
updated-old    1 1 []
updated-new    1 1 ['note.md']
deleted        0 0 []
restored-twice 1 1 ['note.md']
```

At every step auxiliary rowids were a subset of main FTS rowids. Dropping the
auxiliary table and version marker from a populated temporary DB produced no CJK
result before sync, then `['x.md']` and version `1` after explicit sync. Opening
a pre-Phase-2 DB read-only and querying Han returned `[]`; schema inspection was
`before []` and `after []`, proving the read path did not create derived schema.

Temporary roots with project `p`, project `q`, shared scope, and differing tags
produced:

```text
p_no_shared_tag ['p.md']
p_shared_tag    ['p.md', 's.md']
q_no_shared_tag ['q.md']
```

## Cost evidence

Five independent empty-DB rebuilds on the same corpus and machine, checkpointed
and closed before byte counting:

```text
baseline seconds:  0.379027 0.348727 0.326171 0.305242 0.364210; median 0.348727
candidate seconds: 0.426755 0.348930 0.377537 0.382049 0.414953; median 0.382049
baseline bytes:    1,945,600 (all five)
candidate bytes:   2,125,824 (all five)
```

The median rebuild ratio is `1.096x` (budget `<=2.0x`) and storage ratio is
`1.093x` (budget `<=1.50x`).

After one full warm-up, 20 judgment-order rounds of the production base search
call gave inclusive p50/p95 milliseconds:

| group | baseline p50 / p95 | candidate p50 / p95 |
|---|---:|---:|
| overall | 0.317 / 35.447 | 0.383 / 9.743 |
| ASCII | 0.317 / 36.406 | 0.340 / 10.243 |
| Han | 1.523 / 8.854 | 4.780 / 8.842 |

All p95 values are below `max(2 * baseline_p95, baseline_p95 + 10ms)`.

## Unresolved and HANDOFF

- No unresolved Lane A quality, correctness, or cost gate remains on the frozen
  benchmark. The 40 judgments are a limited frozen set and do not establish
  general CJK retrieval quality.
- **Lane B handoff:** the current `memory_store_ext.search_documents` overwrites
  base-layer diagnostics with unconditional `strict` and all-matched values.
  Lane B must stop synthesizing those fields and preserve the values emitted by
  `memory_store.search_documents`; this lane did not modify Lane B's file.
- The benchmark currently imports `memory_store_ext`, so its ranking results are
  valid but its serialized diagnostics remain masked until Lane B completes that
  handoff. Direct base-layer checks confirmed `strict`, `relaxed`, `cjk`, and
  route/gram provenance behavior.

## Round 2 — natural-02 Recall@5 dropout

### Diagnosis and change

The first-round result failed the zero-dropout gate because `natural-02` moved
from rank 5 to rank 8. Direct FTS inspection showed why: after removing the old
content-substring reranker, equal-weight BM25 correctly removed the very large
aggregate documents, but several short documents gained score from generic
function words. The route still included `should`, `I`, and `after`; top short
documents matched five or six routed terms while the expected document matched
four, despite those extra matches not representing the recovery topic.

No coverage threshold, field weighting, stemming, query-specific token, or
parameter search was introduced. The existing small natural-language stopword
set now covers two previously incomplete grammatical classes: common modal
auxiliaries (`can/could/may/might/must/shall/should/will/would`) and common
temporal function words (`after/before/during`). These are ordinary English
function-word classes rather than terms selected from the expected document.
Only lowercase plain-language tokens are eligible, so the existing identifier
protection continues to exclude uppercase/symbol-bearing technical tokens from
rewriting.

This is a ranking-input cleanup, not a minimum-match rule: documents are not
filtered by coverage and the unchanged equal-weight BM25 ranking still orders
all candidates recalled by the remaining OR terms. A focused read-only check
before the edit showed the expected rank was 8 and that generic function-word
matches were contributing to the short-document advantage. No technical terms
such as `sandbox`, `exec`, `recover`, or `timeout` were encoded.

### Frozen gate evidence

The round used the already imported frozen `benchmark.py` and `judgments.json`
from `1c000df`, and a fresh work directory:

```sh
W=$(mktemp -d /tmp/a2-final.XXXXXX)
PYTHONDONTWRITEBYTECODE=1 python3 eval/agent-memory/retrieval/benchmark.py \
  --corpus /home/agent/am/amcorpus --work-dir "$W" \
  --output /tmp/after2-final.json
python3 /home/agent/am/e/eval/agent-memory/retrieval/gate.py \
  --phase 2 /tmp/before.json /tmp/after2-final.json
```

The Lane E `gate.py` path was used because that file is not present at commit
`1c000df` and Lane A is forbidden to add or modify eval files. Exit code was `0`:

```text
"status": "PASS"
"phase": 2
"budget": {"assessed": false}
"failures": []
GATE_RC=0
```

Final frozen metrics and protected ranks:

```text
overall_after {'recall_at_5': 0.925, 'recall_at_10': 0.95,
               'mrr_at_10': 0.8283333333333334}
long-natural-language {'recall_at_5': 1.0, 'recall_at_10': 1.0,
                       'mrr_at_10': 0.425}
natural_ranks [('natural-01', 4), ('natural-02', 5),
               ('natural-03', 1), ('natural-04', 4)]
cjk 1.0 / 1.0 / 1.0
mixed-language 1.0 / 1.0 / 1.0
identifier 1.0 / 1.0 / 1.0
lexical-control 1.0 / 1.0 / 0.9583333333333334
```

All identifier judgments remain rank 1. Lexical-control ranks remain
`1,1,1,2,1,1,1,1,1,1,1,1`; no protected query rank regressed.

### Round 2 unittest evidence

```sh
set -o pipefail
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s eval/agent-memory/tests -p 'test_*.py' 2>&1 \
  | tee /tmp/a2-tests.log | tail -3
```

Exit code `0`:

```text
Ran 37 tests in 1.581s

OK
UNITTEST_RC=0
```
