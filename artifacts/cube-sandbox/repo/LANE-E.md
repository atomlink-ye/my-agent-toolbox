# Lane E — Phase 0 质量基线与验收

## 结论

已从 `1c000df7cf39d9ebdb5a8e1ff237826881793569` 原样导入冻结的 40 条检索基准，真实语料 hash 与 `indexed=523` 均符合 PLAN。生产基线精确值为 Recall@5 `0.8`、Recall@10 `0.825`、MRR@10 `0.7033333333333334`。本次完整机器输出位于 `/tmp/lane-e-measure-final.s8NgSW/measure.json`；SQLite、stdout 等生成物均未加入仓库。

新增 `measure.py` 用生产 `sync_index`/`search_documents` 完成五次独立空库 rebuild、关闭前 checkpoint、存储测量、固定预热一轮后的 20 轮延迟测量及 Han 诊断。新增 `gate.py` 支持 Phase 1/2 的分组门槛、逐 query 窗口保护、控制组排名保护、`1e-12` 容差及（当输入含测量数据时）Phase 2 成本预算。

## 锁定输入和环境

导入前生产 HEAD：`9d4afde1e4e9b21eb9cf4025c5492e676b5c2edb`。

```console
$ sha256sum eval/agent-memory/retrieval/benchmark.py eval/agent-memory/retrieval/judgments.json
798abbf6d0c5dbafefcf4e012200a456e567f7338d2f01bc1ced51a503d2a015  eval/agent-memory/retrieval/benchmark.py
f537a045d07cfe610937c8344005bb136d22736e87319d15f386c7f56ac58290  eval/agent-memory/retrieval/judgments.json
$ python3 -V
Python 3.12.12
$ python3 -c 'import sqlite3;print(sqlite3.sqlite_version)'
3.40.1
```

测量程序退出码 `0`，记录 `input_valid: true`、语料 SHA-256 `150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c`，sync 为 `indexed=523, removed=0, missing_roots=0, roots=2`。

## 精确质量基线

| category (n) | Recall@5 | Recall@10 | MRR@10 |
|---|---:|---:|---:|
| all (40) | 0.8 | 0.825 | 0.7033333333333334 |
| cjk (3) | 0.0 | 0.0 | 0.0 |
| identifier (6) | 1.0 | 1.0 | 1.0 |
| lexical-control (12) | 1.0 | 1.0 | 0.9583333333333334 |
| long-natural-language (4) | 0.75 | 0.75 | 0.175 |
| mixed-language (3) | 0.6666666666666666 | 0.6666666666666666 | 0.6666666666666666 |
| morphology-punctuation (4) | 0.75 | 1.0 | 0.775 |
| paraphrase (3) | 0.3333333333333333 | 0.3333333333333333 | 0.3333333333333333 |
| ranking (5) | 1.0 | 1.0 | 0.7666666666666667 |

逐 query rank（`null` 表示未进 top 10）：

```text
cjk-01=null cjk-02=null cjk-03=null
mixed-01=null mixed-02=1 mixed-03=1
synonym-01=null synonym-02=1 synonym-03=null
natural-01=4 natural-02=5 natural-03=4 natural-04=null
rare-01=1 rare-02=1 rare-03=1 rare-04=1 rare-05=1 rare-06=1
hyphen-01=10 hyphen-02=1 morph-01=1 morph-02=1
exact-01=1 exact-02=1 exact-03=1 exact-04=2 exact-05=1 exact-06=1
exact-07=1 exact-08=1 exact-09=1 exact-10=1 exact-11=1 exact-12=1
rank-01=3 rank-02=1 rank-03=2 rank-04=1 rank-05=1
```

完整 `top_paths`、query、期望路径及 ranks 在上述机器 JSON 的 `baseline.results` 中。

## 成本基线

命令使用全新 `/tmp` work-dir，退出码 `0`：

```console
$ PYTHONDONTWRITEBYTECODE=1 python3 eval/agent-memory/retrieval/measure.py --corpus /home/agent/am/amcorpus --work-dir /tmp/lane-e-measure-final.s8NgSW/work --output /tmp/lane-e-measure-final.s8NgSW/measure.json
```

- 五次完整生产 sync：`[0.466103575, 0.45938146, 0.410928231, 0.469656486, 0.4347997]` 秒；中位数 `0.45938146` 秒。
- 五次 checkpoint/close 后均为 DB `2035712` B、WAL `0` B、SHM `0` B；持久总量 `2035712` B。
- 固定预热一轮、再按 judgment 顺序 20 轮：整体 800 次，p50 `0.000424346` s、p95 `0.039373335` s；纯 ASCII 680 次，p50 `0.000423424` s、p95 `0.040043692` s；含 Han 120 次，p50 `0.000980238` s、p95 `0.011614327` s。
- 原文诊断：两字 `沙箱` 在 8 篇 Markdown 有直接证据，当前 top 10 返回 3 篇；五字 `数据面可用` 在 `agent-server/QA-LEARNING-REMOTE-DEVELOPMENT.md` 有直接证据，当前返回空列表。

S 的 `0.062s` 确为 tokenizer 小实验，不能代表这里测得的完整生产 sync。质量总分、各分组与 DB `2035712` B 均复现 S；未发现这些项目与 S 不一致。补充诊断表明不能把“所有两字 Han 查询都为零”当成当前主索引的一般结论：`沙箱` 因语料中存在可被当前 tokenizer 命中的独立 token 而有结果，但 `数据面可用` 仍复现子串缺口。

## Gate 用法与实证

Phase 1 比较 Phase 0→candidate；Phase 2 比较已验收 Phase 1→candidate：

```bash
python3 eval/agent-memory/retrieval/gate.py --phase 1 BEFORE.json AFTER.json
python3 eval/agent-memory/retrieval/gate.py --phase 2 BEFORE.json AFTER.json
```

输入可为原始 `benchmark.py` JSON，也可为 `measure.py` JSON（后者的质量对象位于 `baseline`）。Phase 2 两端都是 measure JSON 时还判定 DB 总字节、sync 中位数和整体/ASCII/Han p95 预算。PASS 退出 `0`；FAIL 退出 `1`，`failures` 列出具体规则和 query，`query_differences` 列出所有排名变化。

红态基线自比 Phase 1，真实退出 `1`，因为它尚未达到 Phase 1 新门槛：

```text
long-natural-language.mrr_at_10 actual=0.175 minimum=0.35
overall.mrr_at_10 actual=0.7033333333333334 minimum=0.72
```

将 `exact-01` 从 rank 1 构造成 rank 2 的控制组回退，真实退出 `1` 并打印：

```text
rule=control_rank_regression query=exact-01 category=lexical-control before_rank=1 after_rank=2
```

满足 Phase 1 数值且 ranks 不变的合成验收输入真实输出 `status: PASS`，退出 `0`。合成输入仅用于验证 gate 分支，不是改进结果。

## 回归验证

```console
$ PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py' 2>&1 | tail -3
Ran 37 tests in 2.481s

OK
# pipeline 中 unittest 退出码：0
$ git diff --check
# 退出码：0
```

## 未解决项与 HANDOFF

- Phase 1/2 candidate 尚未在本 lane 产生；各 owner 应分别生成 fresh benchmark/measure JSON，再以上述 gate 比较。Phase 2 若只提供 benchmark JSON，质量可判定但成本显示 `assessed: false`；发布验收必须改用两份 measure JSON 完成成本判定。
- 当前 p95 明显高于 p50，后续 candidate 必须在同机相近负载下重测，不应拿 tokenizer-only 或 CLI 启动耗时混比。
- 本 lane 未修改任何 `skills/` 生产代码，也未改 frozen benchmark 的算法、标注或类别。
