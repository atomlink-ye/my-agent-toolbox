# agent-memory 优化裁决与派发契约

本轮只提交方案，不实现。依据为 `RECON-SEARCH.md`（下称 S）与 `RECON-BOOTSTRAP.md`（下称 B）。以下“目标”“门槛”“预算”均为裁决要求，**不是已经测得的改进结果**。报告中未测的指标必须在 Phase 0 补测，不以估算冒充实测。

## 1. 裁决摘要

先交付无需 schema 变更的长句排序修复、真实匹配诊断和可闭环 bootstrap；随后只给 Han 内容增加应用层 bigram 辅助索引。保留 unicode61 主索引、现有 ASCII 精确检索路径及默认等权 BM25。全库 trigram 不采用；本轮不做语义向量、领域同义词表或字段权重搜索。

S §4 的固定基准：523 篇、40 条单相关文档 judgment，Recall@5/Recall@10/MRR@10 为 **0.800/0.825/约 0.7033**。Recall 在此实际等于 Hit@K。CJK 只有 3 条，不能据此声称通用中文检索已解决；但三条全零是明确的功能缺口。S §5 的 2.151% 是 **Han 字符比例，不是中文用户或查询比例**，不能以此拒绝修复。

取舍是为这部分内容建小型辅助索引，而非让全库承担 trigram 的 3.38 倍独立 FTS 空间和 4.0 倍构建耗时。辅助 bigram 的实际成本尚未测，须过预算门槛才能发布。paraphrase 只有 3 条、召回 1/3，且 501/523 篇是短 finding；没有证据表明在这些稀疏共现上实现纯 Python LSA 划算。本轮明确不承诺 paraphrase 提升。

硬边界：仅 Python 标准库与 sqlite3；Markdown 权威、SQLite 可重建；Claude Code、Codex 与裸 CLI 共享 Python 入口；不新增第三方模型、远程 API、daemon、编译 tokenizer。只增加明确要求的检索质量评测能力；**不新增 unittest、bootstrap 自动化测试套件或 A/B 变异臂**。下述 bootstrap/WAL 步骤是本轮要求的隔离验收实跑，不转为新的持久测试文件。

## 2. S §6 检索候选逐项裁决

| 候选 | 裁决 | 采用边界或拒绝/延期依据 |
|---|---|---|
| 只改 query 预处理 | **ADOPT，限缩** | Phase 1 保留原 query 的 AND 优先路径；仅在其空结果后的自然语言回退中去重、处理小型显式停用词集合，停用词删除后为空则保留原词。替换 content 子串计数优先排序，按等权 BM25 排候选。S §3.4 的目标 BM25 -17.9986 强于两个聚合文档却仅 rank 3，长句 MRR 0.175，值得先修。不得全局删除短词、改写错误码或引入泛化 minimum-match/DF 阈值；词形与拼写扩展另行延期。仅改 query **不能**切开已入库 Han token（S §1）。 |
| 显式 unicode61 / Porter | **DEFER** | 继续现有 unicode61 默认行为，不改 diacritic、tokenchar 或加 Porter。identifier 6/6 rank 1，morphology 已 Recall@10=1；为词形换 tokenizer 需全量重建且可能损伤技术词（S §4、§6）。只有收集到稳定的真实词形失败且冻结控制组可保真，才重开。 |
| 内置 trigram FTS | **REJECT，本轮** | S §6 实测独立 FTS 1,355,776→4,583,424 B，构建 0.062→0.248s；2 字 `沙箱/证据/自主/边界` 全零。不能为仅占 2.151% 的 Han 字符使全部英文正文承担此成本，且仍漏掉短中文。不能把这些独立 FTS 数字当成完整 sync/DB 预算基线。 |
| 应用层 CJK bi/tri-gram 辅助索引 | **ADOPT，先 bigram** | Phase 2 仅连续 Han run 生成重叠二元组，文档/query 同一规则；不同时写三元组。主索引保留，2 字 query 产生一个 gram；完整连续命中用原文校验，另保留有覆盖率下限、明确标注 relaxed 的 gram 回退，以处理 S §3.1 的省略 `Sandbox` 查询。S §1 证实索引侧缺口，§5 给出 Han 共 14,788 字，辅助成本有望局部化但尚无实测。单 Han 字符不新建 unigram 路由。 |
| 自定义 CJK tokenizer | **REJECT** | C-level API、编译扩展/第三方 binding 与跨 runtime、零第三方依赖边界冲突（S §6）；已有 stdlib 辅助路线可解决当前 substring 缺口。 |
| BM25 字段加权 | **DEFER；REJECT 固定 (10,5,1)** | S §6 同一 `sandbox` 目标由 rank 18 跌至 32，title 又常在三列重复。不能据单例断言所有权重有害，也不能据“标题更重要”上权重。保留 (1,1,1)；未来必须先有独立训练/冻结验证数据与正文专有词控制，才允许基准驱动调参。本轮不做权重网格、A/B 臂或与 query 修复捆绑调参。 |
| 拆 tags/path/identifier 字段 | **DEFER** | identifier 已 1/1/1，tag 关系表已支持精确过滤（S §2、§4）；没有新增字段的量化收益。会增加 schema、重复文本和同步面，不属于 MVE。 |
| query expansion / 同义词 | **DEFER** | paraphrase 仅 1/3 命中（S §4），但 `cannot connect` 不等价于 `stale binding`；直接把这条 judgment 编进词典会过拟合并扩大错误召回。只有维护者确认稳定的一向别名关系、出现多条独立重复失败并有未用于编词典的验证集时重开；先证明 Recall 增益且控制组无损，再考虑小词典。 |
| RRF 多路融合 | **ADOPT，限 Phase 2 的 word + CJK** | 两路候选不同，raw BM25 不可直接相加（S §6）；按文档 ID 去重并以排名融合。仅含 Han query 开启，纯 ASCII 完全绕过。先固定等权 RRF、k=60 作为设计默认，绝非实测最优值；无第三路、无参数扫描。达不到门槛则不发布 Phase 2。 |
| 纯 lexical LSA | **DEFER** | 523 篇中 501 篇短 finding、CJK 2.151%，双语/改写共现支撑不足；S §6 也没有任何 LSA 改善数字。纯 Python 线代、refit 与数值维护成本无已证收益。只有独立扩大的改写判断集显示词法方案稳定不足、语料共现增加且有可复现的标准库可行性测量，才重新立项。 |
| 随机投影/feature hashing | **DEFER** | S §6 明确其保存 lexical geometry，并不创造同义关系；没有 Recall 增益证据。当前 identifier 已满分，主动引入碰撞不划算。未来须先证明未投影词法相似度确有价值且其空间成为实测瓶颈。 |
| external/contentless 辅助索引 | **DEFER** | 主 FTS 的正文用于检索/诊断；documents 不含 body，SQLite 3.40.1 又不支持 3.43 的 contentless_delete（S §2、§6）。先用普通单列 FTS 存短 gram 串；仅在辅助成本超预算且正确性已达标后单独研究，不改主 FTS。 |
| detail/columnsize 调整 | **DEFER** | 无节省空间/延迟的本语料实测，且减少 detail 会限制匹配能力，columnsize=0 可能拖慢 BM25（S §6）。先保留默认，不在本轮用空间微调扩大兼容矩阵。 |

另采纳 S §1/§7 的正确性修复：`match_mode` 必须来自实际执行路线，OR 不再伪称 strict；matched/missing terms 不得由扩展层无条件填成全匹配。它本身不是质量指标提升，不能计入“搜索变好了”的证据。

## 3. B §5 安装候选逐项裁决

| 候选 | 裁决 | 采用边界或拒绝/延期依据 |
|---|---|---|
| SKILL 开头 Preflight | **ADOPT** | command lookup → 已知 skill 绝对路径的 Python fallback → 分类处理 settings/index。B §3 的缺变量 exit 127、init 后 status exit 2 都需首屏可恢复路径。拒绝照抄报告示意中的宽泛 `status || init`。 |
| 修 setup 的 CWD 与 init/sync | **ADOPT** | B §2/§3 实测 pnpm 10.15.1 从 `/tmp` 链错包，fresh HOME 成功 link 后仍 exit 2。显式进入经 package manifest 校验的 repo root 再 link；按状态分别 init、sync、status，重复执行保留 settings bytes。不把任意 status 失败当未初始化。 |
| 第一等 Python fallback | **ADOPT** | B §2.2/§3.4 真跑证明完整 scripts、Python >=3.10 + SQLite/FTS5 即可运行。独立 skill copy 无 package.json，setup 也须能进入此模式；缺 Node/pnpm 不应阻断核心 bootstrap。不能宣传单文件部署。 |
| read-only fallback 修复 | **ADOPT** | B §3.6 冷库无 sidecar exit 2，已有 WAL+SHM 可读成功。优先 SQLite 基础错误码（含扩展码归一化）；Python 3.10 无错误码属性时有限兼容已知 CANTOPEN/READONLY 文本，其他错误原样失败。仅无/空 WAL且安全条件成立时 immutable，并保留打开后 race recheck，不能吞掉损坏库/未知 I/O。 |
| self-install/bootstrap 子命令 + shim | **DEFER** | 已有完整 Python 入口可用；B §2.3 证明 copy 不提供裸命令，但不妨碍运行。cache 版本过期、复制漂移与 PATH 管理没有最小可验证收益。本轮改已有 setup，不新增 CLI 子命令或稳定 data-dir installer。 |
| CLAUDE_PLUGIN_ROOT 健壮回退 | **ADOPT** | 不再依赖通用 Bash 存在该变量。setup 从自身路径定位 scripts；generic 文案使用加载的 SKILL.md 所在绝对目录；Claude 专用占位符只写在专属示例，未端到端验证须标注（B §6）。禁止用调用 CWD 猜 skill 路径。 |
| Claude plugin-root bin wrapper | **DEFER** | B §6 的 PATH 注入只有文档依据、未实跑；只能覆盖完整 plugin，不能覆盖 Codex/standalone copy。先打通共同 Python 入口，不增 runtime 特例或 package manifest 改动。 |
| doctor opt-in exit policy / ready | **DEFER** | B §4 已证 warning=1 会截断 `&&`，但保留默认 0/1/2 并教会调用者显式处理即可。新增 policy/ready 扩大 API 面；本轮文档不把所有 warning 称为 harmless。 |
| 显式 mutating/bootstrap path 自动 init；init --sync | **ADOPT 前者，DEFER 新 flags** | 只由已有显式 setup 编排 init+sync，普通 search/status/doctor 不隐式写盘。B §3.5 已证 init 仅写 settings，默认读路径无 DB exit 2 是正确边界，不改 init 的既有语义。 |
| guided init / capture unavailable | **DEFER guided init；ADOPT 文档提示** | B §3.5 的空 registry doctor=ok 但无 capture root；文档明确下一步配置 binding/root，不把 status 改成新 schema，也不新增交互向导/flags。 |
| strict path / --global / project 校验 | **DEFER** | B §4 真跑证 read 软回退与 write 严格不一致，但更改默认是兼容风险最高项，且会改变本轮 scope 语义。保留并显著说明 unbound/ambiguous read 会 global fallback；新 API/迁移单独立项，不偷偷收紧。 |
| corrupt DB 恢复提示与陈旧 reference | **ADOPT** | B §3.6 损坏库只报 `file is not a database`，sync 不能原地修复。给出实际 DB path、停止 writers、DB/WAL/SHM 整组 quarantine 后 sync 的指导；更正文档的“所有 read 都跑写 DDL”。不自动移动/删除、不运行 init --force。 |

## 4. MVE-first 分期与实现边界

### Phase 0：锁基线与合同，不改检索行为

1. 从 `recon/r1` 的 `1c000df7cf39d9ebdb5a8e1ff237826881793569` 导入既有 `eval/agent-memory/retrieval/{benchmark.py,judgments.json}`。当前 plan worktree 中它们为未跟踪文件，不能误认为已在基线提交内。实现分支由 E lane 验证与该提交一致后导入；本方案提交不带入它们。
2. 冻结 40 条 query、类别、相关路径与理由，记录两文件 SHA、生产基线 SHA、Python/SQLite 版本。语料 hash 必须为 `150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c`、indexed=523；不符先排查输入，不用新基线覆盖旧分数。
3. 在隔离 work-dir 重跑 S §4，得到精确未舍入指标与逐 query rank，运行现有 37 个 unittest。仅观察：指标预期保持 0.800/0.825/约 0.7033。
4. **需先测**完整生产 sync 秒数、完整 DB/残余 sidecar 大小、search p50/p95（整体及 ASCII/Han 分组）；报告中的 0.062s 是 tokenizer 实验，不能代替它。补记录从同一真实语料抽取的 2 字与 3+ 字 Han 查询的原文证据、当前结果，作为诊断输出，不暗改 40 条判断集。
5. A/B/C/D/E 各 lane 接受下节接口与文件锁后开工。没有基线、语料或可执行环境时记录缺口；不得编造“通过”。

不做：重新普查仓库、网络安装语义依赖、算法试验矩阵、增加新的通用测试套件。

### Phase 1：最小可验证真实收益，可单独发布

- A：仅自然语言 OR 回退去重/有限停用词处理与 BM25 排序；先保留原 AND 的语义、默认等权、候选上限与 scope/tag 过滤。stopword 集不得从某条 judgment 的答案反推；标识符/带符号技术 token 不做词干或停用词改写。增加由实际路由产生的诊断。
- B：扩展层透传诊断；修冷只读连接分类及 corruption 提示。C：setup 的 requirements/路径/状态分类与 Python fallback。D：首屏 preflight、完整目录部署与非破坏性恢复文案。
- 预期且必须核验：long-natural-language MRR@10 **0.175→至少 0.350**，Recall@5/@10 均不低于 0.750；identifier 保持 1/1/1，lexical-control Recall 保持 1/1、MRR 不低于精确基线（约 0.958）。CJK 仍可为 0/0/0；其他组不退步。bootstrap 从 B 中 fresh HOME exit 2、未定义变量 exit 127 的失败变为按合同 status/search exit 0。
- 不做：CJK schema、RRF、tokenizer 更换、同义词、字段权重、CLI 新子命令、scope 行为迁移。如果长句门槛未达，不以安装修复代替检索收益，也不扩大成大重构；保持 Phase 1 未验收。

### Phase 2：补中文缺口，成本受限

- A 独占实现普通 FTS5 单列 `document_cjk_fts(grams)`，rowid=document_id；只保留含 Han bigram 的行。覆盖 S §5 的 Han 范围，按 Unicode 字符切分，不跨标点/ASCII/字段边界；gram 去重，不把 title/brief 重复当额外权重。
- sync 同一事务内插入/更新/删除主、辅助行。用独立派生索引版本标记识别缺表/旧版本；首次升级不能因 mtime/sha 未变就漏填辅助行。版本只管理派生数据，不改 Markdown/frontmatter/settings 版本。
- Han query 即使 word 路由非空，也查询辅助路由，避免 mixed query 被 `sandbox` 词命中短路。先尝试 grams AND，并在原始 title/brief/content 的单一字段中验证完整 run 的连续子串；若无完整命中，再在同一 CJK 路由内做 grams OR，以不同 gram 覆盖数优先、辅助 BM25 次之。暂定覆盖至少一半且至少两个不同 gram；仅一个 gram 的 2 字 query 则必须完整命中。阈值是设计默认、未经实测，不开扫描臂；多个 run 可部分命中但必须披露 missing terms。
- 该回退不可省略：冻结集 cjk-01 是 `本地编辑与远端开发`，S §3.1 的标题含插入的 `Sandbox`；强制所有 grams 或全文连续匹配会把已知目标再次排除，与 Recall@10=1 的门槛冲突。部分 gram 命中标注 relaxed，不能声称 query 的整个 Han term 已匹配；诊断另列 matched grams/coverage。不承诺通用同义中文、任意省略或单字召回。
- word/CJK 两路在**各自候选截断之前**应用相同 project/tag/shared 过滤，各路上限沿用 `max(limit*20,100)`，固定 RRF 以文档 ID 去重；并列按稳定 path 排序。ASCII query 不调用辅助路由，旧 AND 命中顺序不变。对混合路线明确返回来源和融合排名值，不将异构 BM25 当成同一 score。
- 预期且必须核验：CJK Recall@5 **0→至少 2/3**、Recall@10 **0→1**、MRR@10 **0→至少 0.500**；mixed-language Recall@10 **2/3→1**，Recall@5/MRR 不低于 2/3。保住 Phase 1 全部门槛和下节预算。
- 不做：全库 trigram、单字 gram、词典中文分词、第三检索路由、增添 triplet grams、原文格式迁移、持久模型或参数变异臂。

两阶段是顺序增量提交，以相同固定输入比较父版本与当前版本；不是同时维护多个候选变异臂。Phase 2 失败可回滚至合格 Phase 1，但**整轮仍未完成**，不能降门槛冒充完成。

## 5. 五条并行 lane 与唯一写权限

表中路径均为 repo 相对路径。未列文件本轮无人可写；新增文件名也纳入锁。lane 可读任何共享输入，不能为了方便修改别人的文件。集成者只合并提交、运行验收；冲突或接口问题退回唯一 owner，不在集成时跨文件补丁。

| Lane | 目标 | 独占文件面 | 可机器核验的验收 | 明确非目标 |
|---|---|---|---|---|
| A：检索与派生索引 | Phase 1 长句收益；Phase 2 CJK 补召回 | `skills/agent-memory/scripts/memory_store.py`；可新增且仅新增 `skills/agent-memory/scripts/memory_cjk.py` | 固定 benchmark 各阶段门槛；ASCII 原路由不回退；更新/删除/全重建后主辅 docid 一致；scope/tag/shared 无串漏；成本预算过关 | 不碰 ext/CLI/setup/文档/eval；不调权重、不处理安装、不改捕获格式 |
| B：CLI、扩展层与只读安全 | 诊断透传、错误分类、恢复提示 | `skills/agent-memory/scripts/memory_store_ext.py`、`skills/agent-memory/scripts/agent_memory.py` | 真实 OR JSON 不再 strict；实际匹配/缺失 terms 可核对；冷库/安全 WAL/不安全 WAL 三种验收；旧索引只读不建表；37 测试通过 | 不写检索 SQL/gram 算法、不改变 search scope 默认、不改 config/doctor 模块、不加 bootstrap 子命令 |
| C：bootstrap 脚本 | 从任意 CWD 与 standalone copy 完成初始化 | `skills/agent-memory/scripts/setup.sh` | fresh/repeat/missing-index/malformed/corrupt 状态验收；从 `/tmp` link 指向正确 repo；无 Node/pnpm 的 copy 可通过 Python；settings hash 不变 | 不碰 CLI/config/package.json/bin wrapper/文档；不实现 shim 生命周期 |
| D：runtime 使用文档 | 使三类消费者能从 skill 路径推导可执行入口 | `skills/agent-memory/SKILL.md`、`skills/agent-memory/references/doctor-sync-settings.md`、`docs/agent-memory.md`、`README.md` | E 逐字执行 generic/Claude/Codex 文案得到指定退出码；命令路径解析后存在；无普通 Bash 必须设置 CLAUDE 变量的前提；文档检查 requirements、init+sync、capture 边界齐全 | 不碰脚本/manifest/eval；docs 仅持久用户说明，不提交工作报告；不声称未实跑的 runtime 集成成功 |
| E：质量基准与验收执行 | 固定基准、测量预算、输出 gate 结论 | 导入既有 `eval/agent-memory/retrieval/benchmark.py` 与 `judgments.json`；可新增 `eval/agent-memory/retrieval/measure.py`、`gate.py` | 对固定 before/after 输出机器 gate、精确分组值及逐 query 差异；输入 hash 校验；记录 37 tests 与 bootstrap 命令退出码 | 不改生产/文档，不新增 unittest 或 bootstrap 测试 harness，不增加 A/B 臂，不提交生成 DB/JSON/日志 |

**共享基线权限：** benchmark.py/judgments.json 来自 recon/r1，由 E 唯一有权导入；Phase 0 锁定后本轮包括 E 在内均只读，不改算法、标注、类别或相关文档以凑分。新增测量与 gate 通过独立脚本实现，不给 benchmark 加开关。将来若确需改这两文件，只能由 E 提出独立版本变更，另建基线，不能追溯覆盖本轮验收。沿用本任务明确指定的既有 retrieval 路径，不在此轮搬迁；未来新评测输入/元数据遵守 `eval/agent-memory/evals/`。不在 `skills/` 写测试。

### 开工前固定的跨 lane 接口

- A 保持 `search_documents(conn, query, project=None, tags=(), limit=10, include_shared=True) -> list[dict]` 以及 connect/sync 原调用签名。既有 id/path/title/brief/projects/tags 不删；score 保持数值型但文档明确排序来源。
- A 在结果中产生 `match_mode`（`strict` / `relaxed` / `cjk` / `hybrid`）、`matched_terms`、`missing_terms` 及 `retrieval_routes`。strict 仅原 query AND 真命中时使用；原 query 词序去重作为诊断词表，去掉的停用词仍可显示为 missing。匹配检查按对应实际 FTS phrase/gram+子串规则，不能再用任意 Python 子串冒充词匹配。融合可增 `rank_score`，不覆盖既有纯 word 分数语义。
- B 的 ext 只 enrich 元信息并透传 A 的字段，不推断“非空=严格”；不要求改 base 签名。B 可先删伪造字段，待 A 合入后验收完整诊断。纯 text/JSON 输出保持已有顶层形状，警告写 stderr。
- A 缺少/旧版辅助表时仅降级到既有 word 查询，不在 read path 建表；B 在 Han 查询遇到此状态时 stderr 提示显式 sync。新表不加入 B 的只读必需 schema 集，旧索引仍可查询。
- C 通过同目录 Python 入口执行 init/sync/status，复用已有 settings 路径解析，遵守显式 `--settings`（CLI）、`AGENT_MEMORY_SETTINGS`、`AGENT_MEMORY_HOME`、默认 HOME 的优先级；setup 无新 CLI 参数。不能自己维护另一套不一致的默认路径规则。D 按此合同写文案。
- C 只有校验到正确 root package 与 agent-memory bin 映射、pnpm 实际可运行时才执行原有 global link（明确其三个 bin 副作用）；用子 shell 显式 cd。独立 copy 或缺可用 pnpm 走 Python，并明确“Python 入口就绪、裸命令未安装”，不冒称 CLI 已上 PATH。异常 link 不能悄悄报安装成功。
- malformed settings、损坏 DB 与其他 I/O 错误立即失败并给分类提示；只有 settings 缺失才 init，只有 DB 缺失才 sync。已有健康 index 的 setup 不擅自 sync；Phase 2 升级需显式 sync。

派发顺序：E 先锁 Phase 0；A/B/C/D 随后并行。B 的诊断验收依赖 A Phase 1，D 的命令验收依赖 B/C；E 持续只读验收。先合 Phase 1 并 gate，再让 A 交 Phase 2，B/D 仅在各自锁内补兼容提示。A 内部 Phase 1/2 串行，不再拆两个同时写 memory_store.py 的 lane。

## 6. 整轮验收契约

### 6.1 质量最低门槛

所有比较使用 Phase 0 精确浮点值（容差仅 1e-12 的计算误差）；表中三位数只为可读性。**不允许有意质量回退**，不能以总分收益抵销控制组损失。

| 分组（n） | 报告 Recall@5 / @10 / MRR@10 | Phase 1 最低要求 | 整轮 Phase 2 最低要求 |
|---|---|---|---|
| CJK (3) | 0 / 0 / 0 | 不降低 | ≥2/3 / 1 / ≥0.500 |
| identifier (6) | 1 / 1 / 1 | 三项 1；每条仍 rank 1 | 同 Phase 1 |
| lexical-control (12) | 1 / 1 / 约 0.958 | Recall 均 1，MRR≥精确基线；每条 rank 不后退 | 同 Phase 1 |
| long-natural-language (4) | 0.750 / 0.750 / 0.175 | ≥0.750 / ≥0.750 / ≥0.350 | 不低于已验收 Phase 1 |
| mixed-language (3) | 2/3 / 2/3 / 2/3 | 三项不降低 | ≥2/3 / 1 / ≥2/3 |
| morphology-punctuation (4) | 0.750 / 1 / 0.775 | 三项不降低 | 同 Phase 1，不低于实际已验收值 |
| paraphrase (3) | 1/3 / 1/3 / 1/3 | 三项不降低，不承诺提升 | 同 Phase 1，不低于实际已验收值 |
| ranking (5) | 1 / 1 / 约 0.767 | 三项不降低 | 同 Phase 1，不低于实际已验收值 |
| 全部 (40) | 0.800 / 0.825 / 约 0.7033 | Recall 不低于基线、MRR≥0.720 | ≥0.850 / ≥0.925 / ≥0.760 |

额外逐 query gate：已有 Recall@5、Recall@10 命中的条目不得分别掉出对应窗口，允许损失数 **0**；identifier/lexical-control 排名退步数 **0**。其他条目可在相同窗口内换位，但所属分组与整体 MRR 回退阈值仍为 **0**。Phase 2 相对已验收 Phase 1 再执行相同保护。

质量基准仅 40 条，不能对全部用户作统计泛化；没有在冻结集外验证的收益必须写成“此基准上的收益”。paraphrase 保持不退即通过该组，不用无依据的语义承诺填满计划。

### 6.2 成本、重建与既有回归

- 以下是**新预算**：Phase 2 完整持久索引字节数不超过 Phase 0 的 1.50 倍；若重现 B/S 环境完整 DB=2,035,712 B，对应上限 3,053,568 B。统一 checkpoint/close 后计 DB 与残留 sidecar，不能将 baseline 的主库与 candidate 的半个存储面比较。
- 完整 sync 中位耗时不超过 Phase 0 的 2.0 倍。同机同语料各 5 次独立空 DB rebuild，记录全部值与中位数；不使用 tokenizer-only 的 0.062s 代替分母。
- 固定预热一轮后依 judgment 顺序运行 20 轮，测生产 search 调用（不混入 CLI 进程启动）；分别报告总体/ASCII/Han p50/p95。p95 上限为 `max(2 × baseline_p95, baseline_p95 + 10ms)`，各分组分别满足；这是给小样本计时噪声的预算，不是声称已测得的延迟。
- 按 Phase 0 对齐版本/机器/输入。超预算或未测就不能发布 Phase 2；不自动换 trigram/LSA“救场”。
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py'`：现有 **37 tests，全部绿**；不能删、跳过或弱化旧断言。若旧断言与诚实诊断冲突，先记录具体冲突交 owner 裁决，不能擅改测试绕过。
- 在隔离副本中对一篇真实 Han Markdown 做更新、删除、再恢复并显式 sync，检查辅助 rowid 无孤儿、检索反映变化；旧 DB 升级与全新重建得到相同 40 条排名；第二次 sync 不重复堆 gram。只保留命令记录在 `/tmp`，不提交改过的语料或新增测试。
- 对同一临时 root 的 project/tag/`--no-shared` 搜索核对结果所属集合，所有 routes 同一过滤语义。读命令前后比 settings/Markdown/DB 内容 hash，确认没有隐式升级；并核对没有因 fallback 新建 sidecar。

基准复现命令（每次使用新 work-dir；不碰真实 registry）：

```sh
AM_RUN_DIR=$(mktemp -d /tmp/agent-memory-acceptance.XXXXXX)
PYTHONDONTWRITEBYTECODE=1 python3 eval/agent-memory/retrieval/benchmark.py \
  --corpus /home/agent/am/amcorpus \
  --work-dir "$AM_RUN_DIR" --output "$AM_RUN_DIR/metrics.json"
```

E 的 measure/gate 须直接复用固定判断与生产 API，输出精确指标、逐 query 差异、输入 hash、版本及 budget pass/fail，失败退出非零。最终 JSON、日志、数据库和运行报告仅存 `/tmp` 或仓库外；不提交 eval 运行产物。

### 6.3 跨 runtime bootstrap 可核验步骤

每行使用独立临时 HOME/registry；显式清除真实 `AGENT_MEMORY_SETTINGS/AGENT_MEMORY_HOME`，或指定临时值，不继承真实用户配置。记录实际入口绝对路径、版本、stdout/stderr、退出码、settings 前后 hash。chmod/WAL 用有真实权限限制的非 root 身份执行；root 绕过权限的“通过”无效。

| 场景 | 实跑步骤 | 必须观测到的结果 |
|---|---|---|
| requirements | 用实际 Python 检查 `sys.version_info >= (3,10)`；内存 SQLite 创建 FTS5 表；Python 3.10 与报告的 3.12 环境分别跑适用步骤 | 成功后才 bootstrap；不满足时明确缺 Python/FTS5，不写半份 settings，不调用下载器 |
| 裸 CLI/通用 skill、无 Node/pnpm | 把完整 skill 复制到临时目录，PATH 只提供所需 shell/Python 基础工具；unset 两个 Claude 变量；从 `/tmp` 以该 copy 的绝对 setup 路径启动，再重复；以同目录 Python 跑 status/search | setup/status/search 均 0；settings+DB 存在，search 空 registry 为 `[]`；重复 settings bytes 不变；说明裸命令未安装，绝不要求 root package.json |
| 原生 Python 最小入口 | 对同一完整 copy、新 HOME 依次执行下方 init/sync/status/search | 四个退出码均 0；只有 init 不等于 index ready；没有任何第三方 Python 依赖 |
| 仓库全局 link | 隔离 PNPM_HOME，已安装且可工作的 pnpm 10.15.1；从 `/tmp` 调完整 checkout 的 setup，检查 link target 与 `command -v agent-memory`，运行 status，重复 | target 是该 checkout，不是 `/tmp`；status=0；明确 arcp/agent-memory/sandbox-ctl 三 bin 副作用。PATH 未配置时给准确动作，不误用系统另一版本做验证 |
| settings 存在、DB 缺失 | 写有效非空 settings，保存 hash；运行 setup | 仅 sync，status=0、settings hash 一致，未调用 force init；roots 配置保留 |
| 异常 settings / corrupt DB | 分别放格式错误 settings、隔离损坏 DB 后运行 setup/status | 非零；原文件 hash 不变；不得用空 registry 覆盖。损坏提示包括精确 path、停止 writers、整组 quarantine、显式 sync |
| Codex standalone copy | 在隔离 HOME 用报告已跑过的 `npx skills add . --skill agent-memory -g -a codex -y --copy` 部署；核对 copy 只有完整 skill、无 root package；从安装位置按 D 文案调用 Python/setup，运行时移除 Node/pnpm | init/sync/status/search 成功；无需 Claude 变量；真实 Codex 会话能发现 skill 并执行该入口。安装使用 Node 不等于运行期依赖 Node |
| Claude Code plugin | 在真实隔离 Claude plugin 环境加载 skill，记录 runtime 给出的实际 skill 路径；执行专属示例解析后的入口，再按相同 init/sync/status/search 跑一遍；另验证未定义 CLAUDE_PLUGIN_ROOT 的 generic 路径 | 解析后路径存在、命令成功；不把手工 env 注入当成 Claude substitution 已验证。B 的文档依据尚非端到端证据 |
| 冷只读 DB | 完成 sync/checkpoint 并关闭 writer，确认无 sidecars；DB/settings 0444、目录 0555；运行 search/status | exit 0，immutable 警告 stderr；无新文件、不写 schema。未知 I/O/损坏错误不能进入此成功分支 |
| 可读非空 WAL+SHM | writer 写入可检索的唯一文本并 commit，确保该文本尚只在 WAL，保持连接；用只读入口查询 | exit 0 且查到该文本，证明没有误走忽略 WAL 的 immutable；只查 `x` 得 `[]` 不算验证 WAL 可见性 |
| 非空 WAL 无安全读取条件 | 在隔离冻结副本构造非空 WAL、无可用 SHM 且目录不可写；查唯一文本 | 非零并提示在可写环境恢复/sync；不得成功返回不含 WAL 的旧视图；保留打开后 WAL race recheck |
| doctor 与 capture 边界 | 制造一篇尚未 sync 的 Markdown，单独运行 doctor 再独立 search；对无 binding registry 执行 capture | doctor warn=1，search 仍独立执行；capture 明确无目标。文案不用 `doctor && search` 或“所有 warning harmless” |

Python 命令统一如下；`AM_SKILL_DIR` 必须是**已知实际加载目录**，不是未解析的 runtime placeholder：

```sh
python3 "$AM_SKILL_DIR/scripts/agent_memory.py" --json init
python3 "$AM_SKILL_DIR/scripts/agent_memory.py" --json sync
python3 "$AM_SKILL_DIR/scripts/agent_memory.py" --json status
python3 "$AM_SKILL_DIR/scripts/agent_memory.py" --json search x
```

额外检查环境 override 分别生效且写入指定临时路径。临时配置可以用已有配置能力加入一个 root 以核验 search 命中与 capture；不以空 registry=健康证明 capture 可用。

若真实 Claude/Codex 或某 Python 版本不可用，必须记录该项 **NOT RUN** 并由对应 runtime 环境执行；目录布局模拟仅证明 Python 可移植，不能替代发现/占位符集成。整轮“完成”要求所有必需质量、预算、37 测试与上述 runtime 项通过；缺项不能签全绿。

## 7. 风险、迁移与回滚

1. **Phase 1 无 schema 迁移。** 排序/诊断改变可 revert 对应提交；JSON 字段含义纠正需 release note，消费方不能再把 OR 当 strict。scope 默认、init 与 doctor 退出码不改。长句收益若仅来自聚合排序且控制组退步，gate 阻断，不能用平均分掩盖。
2. **Phase 2 加表，不替换主 FTS。** 旧索引仍可读；首次显式 sync 创建/回填全部辅助内容并写版本，过程事务化，失败回滚到完整旧状态。sync 必须从 Markdown 获取 authoritative 内容，不把旧 SQLite 作为永久唯一来源；未知较新版本拒绝写升级并提示匹配版本/重建，不能盲目覆盖。
3. **回滚不要求删除用户索引。** 回滚代码后忽略辅助表，保留原主表语义；需要彻底清理时停止 writers、备份 settings 与 Markdown，并将 DB/WAL/SHM 整组移至 quarantine，再用旧代码 sync 重建。恢复也按整组进行，不能混搭不同时刻的 sidecars。迁移/rebuild 前记录真实路径与当前写入者；本轮不做自动破坏性修复。
4. **WAL 风险优先于可用性。** immutable 忽略 WAL，且开前/开后检查不是对任意并发 writer 的永久安全保证。仅对确认无/空 WAL 的静态只读库回退；无法确认 sidecar 状态、读目录失败或可能有活跃写者时安全拒绝并要求静态副本/可写环境。不能为了让 cold case 通过而无条件 immutable。
5. **安装外部副作用可撤销但需明确。** pnpm global link 会影响三个 bin，并依赖 checkout 生命周期；只在完整仓库路径使用，记录 link 目标。回滚只移除本次创建且仍指向该 checkout 的链接，不卸载别人的包、不删除 registry。独立 copy 的 Python 路径随 runtime cache 升级需重新解析，不创建持久指向旧 cache 的 shim。
6. **不含设计上的不可逆数据变更。** 不改 Markdown/frontmatter、settings schema、capture 内容或 stable IDs，不删除原始文件。索引升级、排序代码与文案均可回退；误删 DB sidecars、init --force 覆盖 settings 等操作在本方案中明确禁止自动执行。
7. **有限基准与语言边界。** 3 条 CJK 和 3 条 paraphrase 很小；bigram 解决连续 Han 子串，不等于中文分词/语义搜索。短 finding 占比与双语分布变化后需另建代表性判断集；当前冻结集继续保留为回归控制，不替换为更容易的新查询。

最终交付由五 lane 的实现提交、固定基准与独立测量/gate 源码、持久使用文档组成；验收输出留仓库外。只有 Phase 1、Phase 2 均满足合同才称本轮完成。本 PLAN 是派发依据，按本次 BRIEF 的例外明确提交根目录；后续运行日志、临时设计与评审笔记不进入 docs 或本文件。
