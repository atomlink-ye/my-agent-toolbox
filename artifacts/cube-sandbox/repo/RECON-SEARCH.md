# Agent Memory 检索管线侦察

日期：2026-09-21。范围：只读生产代码和 `/home/agent/am/amcorpus/`，另新增可丢弃索引上的基准 harness；没有修改 `skills/`。本文所有数字均来自本机 Python 3 / SQLite 3.40.1 的实际运行。

## 摘要

当前系统是三字段 FTS5（`title, brief, content`）上的默认 `unicode61` + 无权重 `bm25()`。先做全词 AND；AND 全空才做 OR，取最多 `max(limit*20,100)` 个 BM25 候选，再按“多少 query term 是 `content` 的 Python 子串”重排。523 篇真实语料的 40 条 judgment baseline 为：**Recall@5 0.800、Recall@10 0.825、MRR@10 0.7033**。但分组差异很大：标识符 1.000/1.000/1.000，CJK 0/0/0，同义改写 0.333/0.333/0.333。

最重要的已证实事实如下：

- `unicode61` 把一段连续 Han 字符当一个 token，中文子串不能正常召回。SKILL.md 所说“手动加空格”只在 query 拆出的某些小词也恰好独立存在于索引时，借 OR 回退碰巧奏效；它不能切开已经作为整段 token 写入的原文。
- 公共返回中的 `match_mode: strict` 不可信：扩展层不知道基础层是否已经走了 OR fallback，却把一切非空结果都标成 strict，并声称所有 term 已匹配。
- 三列 BM25 权重都是 1.0；很多 capture 又把同一标题放在 title、brief、正文 H1 三处，形成未显式声明的三重放大。
- OR 手排只检查 `content`，不看 title/brief；每个 term 只记 0/1，不看频次、边界、IDF。长句 stopword 能把更强 BM25 的正确文档压到聚合文档之后。
- Markdown 仍是权威来源；索引保存完整正文副本（不截断）和 tag aliases，未保存 path；任何候选索引都可以作为可重建派生物，不需要改变 file-first 架构。

## 1. 从 argv 到 SQL 的完整管线

1. `build_parser()` 接收全局 `--settings`；`search` 接收一个 positional query、`--project/--path/--tag/--limit/--no-shared`，默认 limit 10（`skills/agent-memory/scripts/agent_memory.py:155-203`）。`main()` 取 argv、解析格式、规范化 settings path（`:299-306`）。
2. settings 决定数据库位置；只读命令先验证 binding/shared 配置，再以只读 URI 打开已有 DB（`agent_memory.py:370-405`；`skills/agent-memory/scripts/memory_store_ext.py:35-96`）。
3. scope：显式 `--project` 原样使用；无 `--path` 是全局搜索；有 `--path` 时调用 binding resolution。路径未绑定或是多个 binding 的共同祖先时，**read-only 查询回退为全局 scope (`None`)**，而不是报错（`agent_memory.py:264-275,411-417`）。同时给 `--project` 与 `--path` 才报错。
4. `_fts_terms` 是 `re.findall(r"[\w-]+", text, re.UNICODE)`：保留 Python `\w` 与连字符形成的片段，丢弃其他符号。`_fts_query` 把每片双引号包住，用 AND 或 OR 拼接（`skills/agent-memory/scripts/memory_store.py:309-320`）。例如 `AGENT_CREATE_FAILED` 先保持一个 Python term，但 `unicode61` 又会把下划线当 separator；`read-only` 在 FTS 中成为相邻的 `read only` phrase。
5. SQL 将 `document_fts.rowid` 与 `documents.id` 连接，`MATCH ?`，按 scope/tag 的 `EXISTS` 子查询过滤，最后 `ORDER BY score LIMIT ?`；项目搜索默认也含 `_shared`（`memory_store.py:384-401`）。其核心是：

   ```sql
   SELECT d.id,d.path,d.title,d.brief,document_fts.content AS indexed_content,
          bm25(document_fts) AS score
   FROM document_fts JOIN documents d ON d.id=document_fts.rowid
   WHERE document_fts MATCH ?
   ORDER BY score LIMIT ?
   ```

6. 先执行严格 AND，任何一条命中都会直接返回，不再寻找只缺一个词但可能更相关的文档（`memory_store.py:403-405`）。只有零命中才 OR 召回 `max(limit*20,100)` 条；随后按 `(匹配的不同 term 数降序, bm25 升序)` 排序，其中 term match 是 `term in indexed_content.casefold()`，即 content-only、无边界的 Python 子串布尔计数（`:407-422`）。SQLite FTS5 的 BM25 因实现中取负，**越小/越负越好**。
7. 扩展层拿到基础层结果后，无论它来自 AND 还是 OR，统一写入 `match_mode="strict"`, `matched_terms=全部`, `missing_terms=[]`（`skills/agent-memory/scripts/memory_store_ext.py:161-169`）。因此这些诊断字段不能证明严格匹配。

### FTS 建表与 tokenizer

DDL 是精确的 `CREATE VIRTUAL TABLE ... USING fts5(title, brief, content)`，没有 `tokenize=`、`content=`、`detail=` 或 `columnsize=`（`memory_store.py:66-68`）。所以使用 FTS5 默认 `unicode61`；本机 `document_fts_config` 只有 `('version',4)`。SQLite 官方说明 `unicode61` 把 Unicode 6.1 的字母/数字/Co 类别组成的每段连续字符视为一个 token；Han 属于 Lo，所以它不做中文分词。[SQLite tokenizer 文档](https://www.sqlite.org/fts5.html#tokenizers)；[unicode61](https://www.sqlite.org/fts5.html#unicode61_tokenizer)。

实际隔离实验：

```console
$ python3 - <<'PY'
import sqlite3
c=sqlite3.connect(':memory:')
c.execute('create virtual table u using fts5(x)')
c.execute("insert into u values ('中文查询测试')")
c.execute("create virtual table uv using fts5vocab(u,'instance')")
print(sqlite3.sqlite_version)
print(list(c.execute('select * from uv')))
for q in ['中文查询测试','中文','查询测试']:
    print(q, list(c.execute('select rowid from u(?)',(q,))))
PY
3.40.1
[('中文查询测试', 1, 'x', 0)]
中文查询测试 [(1,)]
中文 []
查询测试 []
```

因此 SKILL.md 的核心断言成立：连续中文是不可拆 token。但“手动空格分词”不是普遍修复：给 query 加空格只会生成多个短 query token，不能改变索引中原文的整段 token。真实语料中 `自主边界` 能命中 1 篇，是因为诊断文档把它另行独立引用；`只给约束不给自主边界` 等较长词条仍作为整体出现在 `fts5vocab`。

`bm25(document_fts)` 没有额外列权重，因此三列均为 1.0；SQLite 的固定参数是 k1=1.2、b=0.75。传 `bm25(document_fts, X, Y, Z)` 才能分别加权，且无需重建。[SQLite BM25 文档](https://www.sqlite.org/fts5.html#the_bm25_function)。

### 真正写入 FTS 的内容

sync 遍历每个 root 下全部 `*.md`，同一物理路径可合并多个 scope/root tag（`memory_store.py:215-240`）。frontmatter 被移除后：

- `title`：frontmatter `title`；否则首个 H1；否则把文件名 stem 的 `-/_` 换为空格。
- `brief`：frontmatter `brief`；否则首个非空、非 heading 段落，压成一行并截到 280 字符（`memory_store.py:107-133`）。
- `content`：**完整、未截断 body** + 两个换行 + tag aliases（`memory_store.py:259-263`）。aliases 包括完整 tag、冒号各 segment、segment 再按 `-/_/空白` 切出的片段（`:136-150`）。
- path 不进 FTS；tags 同时保存在关系表，scope 仅来自 settings/root；frontmatter 不决定 project。frontmatter 本身不进入 content，但解析出的 title/brief/tags 分别进入对应字段。

## 2. 索引与文档模型

`skills/agent-memory/scripts/memory_store.py:26-69` 定义核心 schema：

| 表 | 实际列/约束 | 用途 |
|---|---|---|
| `documents` | `id INTEGER PK`, `path TEXT UNIQUE NOT NULL`, `title`, `brief`, `mtime_ns`, `size`, `sha256` | 文档元数据与增量/陈旧判断 |
| `document_fts` | FTS5 `title, brief, content`；rowid 对齐 document id | 普通（非 external/contentless）FTS，保存自己的文本副本 |
| `document_scopes` | `(document_id FK ON DELETE CASCADE, scope)` 复合 PK，scope index | 一个物理文档可见于多个 project/shared scope |
| `document_tags` | `(document_id FK ON DELETE CASCADE, tag)` 复合 PK，tag index | canonical tag 过滤与展示 |

扩展层还建 `memory_meta`（stable id/type/lifecycle）和 `memory_links`，但它们不参与检索评分（`memory_store_ext.py:26-31`）。

### capture 生成格式与真实样例

capture 的 summary 先成为 title（只清换行与双引号），`brief=title[:280]`；类型映射到 `learnings/drawbacks/errors/feature-requests` 子目录，文件名为 UTC 时间 + ASCII slug（`skills/agent-memory/scripts/memory_capture.py:133-179`）。结构固定为 frontmatter、H1、Kind/Status/Logged/Project，然后可选 Why、How to apply、Related Files：

```markdown
---
id: mem_...
title: "..."
brief: "..."
type: learning
status: raw
tags: [project:learnings, self-improvement:learning, ...]
---

# ...

**Kind**: learning
**Status**: raw
**Logged**: ...
**Project**: ...

## Why
...
## How to apply
...
```

真实样例：

1. `agent-server/drawbacks/20260820-140411-agent-memory-fts-cjk-indexed-as-whole-punctuation-delimited-runs.md:1-23`：完整 capture frontmatter（id/title/brief/type/status/tags）、H1、固定 metadata、中文 Why 与 How to apply。
2. `agent-server/learnings/20260825-015616-sandbox-ctl-exec-recovery-classify-control-failures-and-use-exec.md:1-23`：同一模板，四个 tags，英文 Why/How。
3. `arcp/errors/20260908-stale-binding-silently-swallows-a-write-command.md:1-37`：无 frontmatter；H1 成 title，首个正文段落成 267 字符 brief，随后是三个二级章节。它证明 fallback metadata 路径是真实在用的。

### project、scope、capture root 与 `--path` 回退

settings 中每个 binding 有绝对/展开后的 path、project、memory roots、tags 和嵌套深度；child 可选择继承 parent memory，tags 会继承（`skills/agent-memory/scripts/memory_config.py:166-217`）。索引 root 转成 `MemoryRoot(path, scope=binding.project, tags=...)`；shared scope 固定 `_shared`（`:220-252`）。

`resolve_binding` 对路径内部采用最长前缀；若目标是唯一 binding 的祖先也可解析；若是多个 binding 的祖先则歧义；完全不相关则 unbound（`memory_config.py:263-305`）。写操作遇到歧义/未绑定会停止，read-only `search/list/tags/browse --path` 则由 `_query_project` 吞掉这两种异常并做全局查询。capture 优先选当前 settings node 唯一 `capture:true` root（`skills/agent-memory/scripts/memory_admin.py:61-97`）；没有标记时，有且仅有一个有效 root 可省略 `--root`，多个必须显式指定（`memory_capture.py:27-44`）。

## 3. 红先行：可复现失败模式

先运行基准以生成隔离 settings/DB；它不会读取或写入 `~/.agent-memory`：

```bash
cd /home/agent/am/r1
AM_TMP=$(mktemp -d /tmp/agent-memory-recon.XXXXXX)
python3 eval/agent-memory/retrieval/benchmark.py \
  --corpus /home/agent/am/amcorpus --work-dir "$AM_TMP" \
  --output "$AM_TMP/baseline.json" >/dev/null
AM="python3 skills/agent-memory/scripts/agent_memory.py --settings $AM_TMP/settings.json --json search"
```

下列输出是本次真实运行所得。为了使显示可读，`rank` 是对 CLI JSON 中 path 的顺序计数；标题/score 均取原结果。公共 CLI 把以下 OR fallback 全部错误标为 `strict`，故不再逐项重复该字段。

### 3.1 整句中文

```console
$ $AM '数据面可用' --limit 10
[]
```

期望：`agent-server/QA-LEARNING-REMOTE-DEVELOPMENT.md`（正文明确写“`state=running` 不证明数据面可用”）；实际 0 hit。`fts5vocab` 的单行实验已证明这不是语料缺失，而是连续 Han token 无子串匹配。真实标题 `本地编辑与远端 Sandbox 开发` 上，`本地编辑与远端开发` 也是 0；加空格后可能靠独立的 `开发`/ASCII `Sandbox` 走 OR 召回，但不是把索引原词切开。

### 3.2 中英混排

```console
$ $AM 'sandbox 凭据 故障恢复' --limit 10
1  Credential in evidence archive ...                         score=-7.4468
2  agent-memory FTS: CJK indexed as whole ...                 score=-6.5572
3  Agent Server 新 campaign ...                               score=-2.9910
4  多条 lane 跑在各自沙箱里 ...                               score=-2.9702
5  nohup 起栈前忘记 source 凭据文件 ...                        score=-2.3642
```

期望同一 QA workflow；实际默认 top 10 没有它，扩至 200 时 rank 45。CJK 两段未匹配，OR 基本退化成高频 `sandbox`。

### 3.3 同义/近义改写

```console
$ $AM 'sandbox cannot connect' --limit 10
1  PR119 runtime canaries need a stable real database ...      -6.2424
2  sandbox-ctl cube-sandbox e2b facade blocker ...             -5.2298
3  Cluster says running, adopt says gone ...                   -5.0155
4  Agent Server QA Learning ...                                -3.1776
5  Errors                                                       -1.3122
```

期望 `arcp/errors/20260908-stale-binding-silently-swallows-a-write-command.md`（文档写的是 stale/expired binding、daemon unavailable）；默认 top 10 未出现，扩至 200 是 rank 16。纯 lexical 管线不知道 `cannot connect` 与 `stale binding` 的近义关系。

### 3.4 长自然语言 + OR 手排

```console
$ $AM 'the sandbox cannot connect because stale binding silently swallows a write command' --limit 10
1  Errors                                                       -5.8676
2  Learnings                                                     -3.3393
3  A stale Cube binding reports "daemon unavailable" ...       -17.9986
4  A Manager cannot see what its child Task actually did ...   -10.1903
5  Agent Server QA Learning ...                                 -9.4660
```

期望 stale-binding 文档，实际 rank 3。它的 BM25（-17.9986）远强于两个聚合文档，却被 content 子串计数压后；`the/because/a` 等词参与手排。因为 AND 全空才进入 OR，这也是为何 score 顺序看似反常。

### 3.5 单个高频词

```console
$ $AM 'sandbox' --limit 100
hits=86; stale-binding expected document rank=18
top 3:
1  Cube Sandbox running state does not prove ...                -3.3156
2  sandbox-ctl cube-sandbox e2b facade blocker ...              -3.2817
3  sandbox-ctl binding + pull friction ...                       -3.1990
```

`sandbox` 无法表达用户寻找哪个 symptom；专门解释 stale binding/write outcome 的文档被 17 篇文档淹没。类似 `error` 有 113 hits；此类 query 需要字段/IDF/意图约束，而不是更多 OR。

### 3.6 词形与连字符

```console
$ $AM 'readonly lookup' --limit 10
# expected authorization-axis document: rank 10
$ $AM 'read-only lookup' --limit 10
1  The authorization-outside-transaction axis is closed        -7.3489
$ $AM 'connections terminate unexpected' --limit 10
1  Errors                                                       -1.4976
```

前两条是同一信息需求，拼写差异使 rank 1 变 rank 10。第三条期望 `agent-server/errors/20260820-192324-chat-delivery-worker-failed-connection-terminated-unexpectedly-s.md`（原词为 `connection terminated unexpectedly`），实际目标完全缺失，只剩聚合 `ERRORS.md`。反例也要保留：`reminder wakeup sources` 的期望文档 rank 1，说明 OR 能救回部分复数变化；词形行为不一致，而非全面失效。

### 3.7 标识符与符号

```console
$ $AM 'AGENT_CREATE_FAILED' --limit 10
1  Long ARCP goals exceed Paseo tab title limit                 -6.9518
$ $AM '@electric-sql/pglite-socket@0.2.7' --limit 10
1  PR #100 的 host-native 改造把 PostgreSQL 的获取责任留空 ...  -18.6106
2  PostgreSQL 08P01 in PostgresChatDispatchRepository.claimNext ... -17.0723
```

稀有大写错误码成功 rank 1。复杂 package/version query 则在 `_fts_terms` 和 `unicode61` 两层丢弃 `@ / .` 边界，直接解释该版本/08P01 的文档仅 rank 2。`sandboxctl` 是 0，而 `sandbox-ctl` 与 `sandbox_ctl` 都按 `sandbox`+`ctl` 检索；符号保真取决于两套 tokenizer 的偶然组合。

## 4. 可复现 baseline

提交物：

- `eval/agent-memory/retrieval/judgments.json`：40 条 query→单一 expected document，每条有 category 与内容依据；expected path 启动时逐一验证存在。
- `eval/agent-memory/retrieval/benchmark.py`：仅标准库；写临时 settings，把两个 corpus 子目录绑定到 `arcp`/`agent-server`，调用生产 sync/search，在指定 `--work-dir` 下重建独立 SQLite，输出每条 top 10、rank 与总指标。

运行命令：

```bash
python3 eval/agent-memory/retrieval/benchmark.py \
  --corpus /home/agent/am/amcorpus \
  --work-dir /tmp/agent-memory-benchmark \
  --output /tmp/agent-memory-baseline.json
```

本次实际 sync：`indexed=523, removed=0, missing_roots=0, roots=2`；harness 对“相对路径 + NUL + 原始 bytes + NUL”的有序全集计算出的 corpus SHA-256 是 `150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c`。一个 query 只标一个相关文档，因此这里的 Recall@K 数值也就是 Hit/Success@K；MRR@10 是该文档首次出现的 reciprocal rank，未进 top 10 记 0。总指标与逐 category 指标都由 harness 直接输出。

| category | n | Recall@5 | Recall@10 | MRR@10 |
|---|---:|---:|---:|---:|
| **全部** | **40** | **0.800** | **0.825** | **0.703** |
| CJK | 3 | 0.000 | 0.000 | 0.000 |
| identifier | 6 | 1.000 | 1.000 | 1.000 |
| lexical-control | 12 | 1.000 | 1.000 | 0.958 |
| long-natural-language | 4 | 0.750 | 0.750 | 0.175 |
| mixed-language | 3 | 0.667 | 0.667 | 0.667 |
| morphology-punctuation | 4 | 0.750 | 1.000 | 0.775 |
| paraphrase | 3 | 0.333 | 0.333 | 0.333 |
| ranking | 5 | 1.000 | 1.000 | 0.767 |

12 条 exact/lexical control 防止未来改动破坏已有强项，但会抬高 aggregate；任何方案都应同时看分组指标，不能只看 0.800。baseline 是固定语料上的判断集，不声称覆盖所有真实用户意图；未来可追加 multi-relevance judgments，但不应悄悄改写本组数字。

## 5. 语料统计

统计口径：UTF-8 replacement read；CJK 为 U+3400–4DBF/U+4E00–9FFF/U+F900–FAFF；word 是正文 `\w+`；size 是磁盘 bytes。

| 项目 | 实测值 |
|---|---:|
| Markdown documents | 523 |
| scopes | agent-server 357；arcp 166 |
| 总大小 | 721,932 bytes |
| 平均 / median / p90 / max | 1,380 / 1,116 / 1,889 / 31,376 bytes |
| 总字符 / CJK 字符 | 687,598 / 14,788（2.151%） |
| 平均 / median / p90 正文 words | 147.7 / 109 / 214 |
| frontmatter type | learning 340；error 90；drawback 35；feature-request 14；workflow 1；missing 43 |
| 当前独立 DB 大小 | 2,035,712 bytes |
| 平均 FTS title / brief / content 字符 | 80.4 / 90.5 / 1,164.4 |

为了把“长 workflow vs 短 finding”变成可复算而不是主观标签，本文定义：长 workflow = `size>=8192` **或**至少 8 个 H2；短 finding = `size<=3072` **且**最多 3 个 H2；其他为中间档。结果：**长 3、短 501、中间 19**。这说明语料以短、标题高度描述性的 finding 为绝对多数；少数聚合/workflow 很长，会在 OR 与高频词中制造噪声。

tag 分布头部（document count）：`self-improvement:learning 340`、`agent-server:learnings 252`、`arcp:learnings 88`、`self-improvement:error 84`、`agent-server:errors 74`、`agent-server:operations 44`、`agent-server:runtime 35`、`agent-server:frontend 29`、`self-improvement:drawback 28`、`agent-server:drawbacks 24`、`agent-server:workflow 24`、`sandbox:runtime 19`。这些 tag aliases 被追加到 content，既提高分类词 recall，也会影响正文 BM25/手排。

## 6. 候选改进方向（提案，不下结论）

以下都保持 Markdown 权威、SQLite 可删除重建。应逐项在同一 judgment 上测 Recall/MRR，并同时记录 DB bytes、全量 rebuild 秒数、query p50/p95；不要把多项一起上线后才猜贡献。

| 方向 | 机制 / 对应失败 | 依赖成本 | 索引与重建影响 | 主要风险 |
|---|---|---|---|---|
| **只改 query 预处理** | Unicode/case 规范化；生成 `read-only/readonly` 变体；去重/降权 stopword；保护错误码；AND→minimum-match/OR；按 DF 丢弃高频词。针对长句、词形、连字符、高频噪声。 | 纯 stdlib | 无 schema/重建 | **不能**召回已作为单个 token 索引的 CJK 内部子串；规则/stopword 可能伤害标识符，放宽会增噪声。 |
| **显式 unicode61 / Porter** | 固定 diacritic/punctuation 配置；可测试 `porter unicode61` 处理 English inflection。 | SQLite built-in | 换 tokenizer 要全量重建；规模同阶 | unicode61 选项不会中文分词；Porter 只适合英文，会过度 stem 技术词/标识符；把 `-_` 设 tokenchar 会改变现有行为。 |
| **内置 trigram FTS** | 辅助三元子串索引，召回连续中文、mixed string、identifier fragments。[官方 trigram 文档](https://www.sqlite.org/fts5.html#the_trigram_tokenizer)。 | 本机 SQLite 已验证 built-in | 实测同一 523 文档：build 0.062→0.248s（4.0x），独立 FTS 1,355,776→4,583,424 B（3.38x）；全量重建 | 少于 3 个 Unicode 字符的 MATCH（`沙箱/证据/自主/边界`）均 0；substring 假阳性；三元 BM25 与 word BM25 不可直接比较。`detail=none/column` 又限制长 MATCH token。 |
| **应用层 CJK bi/tri-gram 辅助索引** | 检出 Han runs，对文档与 query 生成一致的空格分隔 2/3-grams；保留 unicode61 主索引。解决 2 字中文与长段内部子串。 | Python stdlib + sqlite3 | 新增 postings 约随 CJK 字符×gram 阶数增长；全重建 | 边界和 score calibration 自己维护；bigram 噪声/过匹配；两个索引 docid 必须同步。 |
| **自定义 CJK tokenizer** | 真正词切分，或 colocated word+gram/synonym。 | FTS5 tokenizer 是 C-level API；需编译/loadable extension 或第三方 binding/词典，非 stdlib | 全重建；大小依 token/同义词策略 | 最高的 ABI、部署、词典版本与可复现成本；extension loading 可能被禁。 [custom tokenizer](https://www.sqlite.org/fts5.html#custom_tokenizers) |
| **BM25 字段加权** | 查询改成 `bm25(document_fts,title_w,brief_w,content_w)`，提高短 title/brief，压长 workflow。 | SQLite only | 不改索引、不重建 | 只改排序不造 recall；当前 title 常在三列重复。实测 `sandbox` 的 stale-binding 文档从等权 rank 18 在 `(10,5,1)` 下反而 rank 32，因为关键词只在正文；必须用基准调参。 |
| **拆 tags/path/identifier 字段** | tags 单列以独立加权；可给 path/identifier aliases 专门 route。 | SQLite only | schema + 全重建，适度重复 | path/tag 噪声；关系表已能精确 tag filter，收益需实测；migration/debug 面增大。 |
| **query expansion / 同义词** | 维护小型 domain map（如 cannot connect↔stale binding、缩写、错误别名），原 query 高权 route + expansion OR route。 | stdlib dict/JSON | query-time 无重建；若 index-time materialize 则增大索引 | 维护与 domain drift；双向扩展会爆炸/降 precision。FTS 原生 colocated synonyms 需要 custom tokenizer；应用层 OR 更轻。[synonym support](https://www.sqlite.org/fts5.html#synonym_support) |
| **RRF 多路融合** | 分别取 strict word、relaxed word、title/tag exact、CJK grams/trigram、synonym expansion 的 rank，用 reciprocal rank fusion，不比较异构 raw score。 | 融合纯 stdlib | RRF 本身无；辅助 route 才增索引 | 多查询 latency/复杂度；候选上限与 RRF k 要调；多个同质弱列表没有价值。应保留 route provenance 便于解释。 |
| **纯 lexical LSA** | corpus TF-IDF 稀疏矩阵→低秩潜变量→cosine 候选，再与 BM25/RRF 融合；目标是英文 paraphrase/co-occurrence。 | 纯 Python 可写但线代代码较重；NumPy 稳健但新增依赖；禁止大模型/API | vocab、IDF、vectors 存 SQLite；corpus 变动时 refit；523 短文存储不大但 build CPU 增加 | 稀有标识符会被模糊；仅 523 短文且双语共现稀疏；纯 Python SVD/幂迭代的数值稳定与维护风险较高。 |
| **随机投影/feature hashing** | TF-IDF lexical 向量确定性投影到小型 dense vector，cosine + RRF；是较小的统计替代。 | stdlib 可行 | 固定维向量，重算 IDF/投影；存储有界 | 保留 lexical geometry 而非创造真正 synonym 语义；collision/noise；可能不如 LSA，但实现更小。 |
| **external/contentless 辅助索引** | auxiliary gram index 可考虑不重复全文；external content 指向普通表，contentless 只留 postings。[官方说明](https://www.sqlite.org/fts5.html#external_content_and_contentless_tables) | SQLite only | 可少一份正文；schema + 重建 | 现有 `documents` 没有 body；external 必须严密同步。contentless 列读回 NULL，会直接破坏当前 content reranker；本机 SQLite 3.40.1 又早于 `contentless_delete=1` 的 3.43，普通 DELETE/UPDATE 流程不兼容。 |
| **detail/columnsize 调整** | auxiliary gram index 测 `detail=column/none` 降空间，保留 columnsize 供 BM25。 | SQLite only | 重建，可能缩小 | phrase/NEAR/column filter 能力受限；trigram 在低 detail 有额外 MATCH 长度限制；`columnsize=0` 可能让 BM25 更慢。[detail](https://www.sqlite.org/fts5.html#the_detail_option) |

候选之间存在互补而非单一替换关系：word unicode61 对稀有 ASCII identifier 很强；CJK bi/tri-gram 补中文 substring；query expansion/LSA 才触及真正 paraphrase；字段权重只改排序；RRF 只在这些 route 确实多样时有意义。以上是待基准验证的设计空间，不是选型结论。

## 7. 复核要点

- 重跑 `benchmark.py` 后应看到 `indexed: 523` 与上述 40 条指标；若 corpus 变化，应记录新的 corpus SHA/文件清单并把数字视为新 baseline。
- 公共结果中的 `match_mode` 暂不能作为 AND/OR 证据；要验证 mode，需直接复现生成的 MATCH expression 或增加不改变检索的诊断 instrumentation。
- 比较 tokenizer 时必须同时测 2 字 CJK、3+ 字 CJK、完整 identifier、英文词形和长 query；只展示 trigram 的 3+ 字成功样例会遗漏它的已验证短 query 盲区。
- 任何 SQLite 优化都应从 Markdown 全量重建后验证；不要把已有 DB 当新的权威来源。
