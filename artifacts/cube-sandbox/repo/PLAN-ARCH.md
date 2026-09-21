# agent-memory 架构裁决：Claude Code 使用方式，全局存储，跨 agent

状态：方案定稿，待实现。本文是本次唯一交付；不表示下述新命令、模块或验收脚本已经存在。实施分支为 `plan/arch`；本次不修改代码或 `eval/`。

## 1. 裁决摘要与证据边界

**采用「无参数 brief → global + project 有界索引摘要 → 普通 Read 按需读 topic → 必要时 FTS5」作为日常回路；任何零命中都必须提供导航出口。** Markdown 保持事实源，SQLite 保持可重建缓存。跨 runtime 的共同协议是 agent 主动调用 CLI；禁止写入 CLAUDE.md、AGENTS.md、runtime 配置或任何用户自己的文件，也不生成交给 runtime 自动加载的文件。仅在 agent-memory 自己管理的库内维护 MEMORY.md，由命令读取。

新 topic 使用 Claude 的 `name / description / metadata.type`，四类固定为 `user / feedback / project / reference`。旧 `kind` 保留为正交的 `event_kind`，不把 523 篇历史事件机械改名成四类事实。历史材料无损迁入可搜索 archive，已有身份、scope、链接和 provenance 保留；不要求用户逐篇归类。

证据优先级：`CORRECTION.md` 的用户修正 > `BRIEF.md` 的真实 Claude Code 2.1.263 实验 > 本仓库实现 > `RESEARCH-MEMORY-DESIGN.md` 的建议。修正明确指出 skill description 已能触发调用；问题是 agent 猜一次关键词、零命中后放弃，不是缺少宿主加载入口。接受 brief 中全部事实，包括 repo 子目录、同 repo worktree 共享 memory、非 git 使用 resolved path，以及 Claude 原生回路没有检索。下文 FTS 是我们的补充能力，不能反推 Claude 有搜索。目录扁平化算法不是接口，不采用。

已只读核实的实现事实：

- 当前 settings v1 可把 Markdown 放在任意目录，默认 DB 是 `index.sqlite3`；`bindings` 支持嵌套项目、相对 roots、继承 tags 和可选 `inherit_memory`。
- `_shared` 是全局可见 scope；一个物理源可以属于多个 scope。当前某些未绑定或歧义路径会搜索全部项目，必须关闭这种隐式扩域。
- `capture` 已写 `mem_<uuid>`；SQLite `documents.id` 只是本地缓存行号。禁止拿行号作为迁移身份。
- `memory_store.py` 的 frontmatter parser 是平面解析，`memory_lifecycle.py` 的字段更新也不识别嵌套。只添加 `metadata.type` 会出错，必须一起升级。
- sync 递归收集所有 `*.md`，必须明确排除路由索引和管理文件；移动文件会影响相对链接与 wiki link。
- `snapshot` 可能报告缺失 roots、不可读 roots、跳过文件，且没有 restore 命令；不能直接宣称现有 snapshot 已经解决回滚。
- CJK bigram + RRF、跨 runtime bootstrap、紧凑输出是已验收成果，保留。调研中建议「先做 CJK」及 skill 中相应旧提示不能作为重做理由。

## 2. Q1：存储、项目身份与 scope

### 2.1 最终布局

采用下列布局。`AM_HOME` 是文档中的名称；实际环境变量继续用 `AGENT_MEMORY_HOME`。现有 `--settings`、`AGENT_MEMORY_SETTINGS` 优先级保持，不强行把自定义库迁回默认目录。

```text
~/.agent-memory/
├── settings.json                  # schema v2、兼容 bindings、当前 generation
├── projects.json                  # 持久 project ID、locator/旧项目别名；不是 SQLite
├── global/
│   ├── MEMORY.md                   # 纯路由索引，无 frontmatter、无记忆正文
│   └── topics/<name>.md
├── projects/<project-id>/
│   ├── MEMORY.md
│   └── topics/<name>.md
├── archive/legacy/<source-root-id>/.../*.md
├── archive/legacy/manifest.json    # 原路径→归档路径、scope 集、有效 tags、hash、旧 ID
├── index.sqlite3                  # 沿用文件名，可整库重建
├── context-cache/                 # P1 的兼容索引投影，可丢弃
└── migrations/<migration-id>/     # journal、完整备份与切换记录，保留至显式清理
```

`topics/*.md` 和归档 Markdown 是事实源；settings、registry、manifest 是持久路由/迁移配置。SQLite 丢失不能丢 scope 关系，必须能从这些文件重建。`MEMORY.md` 是可审阅的路由投影，不是事实或状态的唯一载体；pin、排序输入、状态等需要保留在 topic metadata / manifest 中。索引采用幂等原子生成，手工添加的 hook 应通过 topic description/pin 持久化，不能偷偷以 SQLite 为真源。

archive 是兼容空间，不是第三个默认 scope。每份原物理文件只保存一次，manifest 保留它原来所属的 scope 集和继承后的 tags。项目索引可链接有权读取的 archive 条目，不复制出多个同 ID 文档。`MEMORY.md`、备份、context-cache、管理说明全部排除在 FTS topic 枚举之外。

### 2.2 项目身份：持久 UUID + canonical git common directory

**ADOPT：registry 分配并持久保存 `prj_<uuid>`，由 locator 查找，不由路径字符串直接充当目录名。**

解析顺序固定：

1. 显式 `--project <id-or-unambiguous-alias>`；未知 ID 报错，不新建错别字项目。
2. 对真实 `--path` 或 cwd 做 realpath。git 内使用 `git rev-parse --path-format=absolute --git-common-dir` 后 realpath 为本机 locator，记录主 worktree root 供诊断。这样子目录、linked worktree、symlink 路径得到同一个 locator。不能直接 hash 当前 `--show-toplevel`，因为每个 worktree 的值不同。
3. 查 registry。兼容期用 v1 bindings 补齐 locator→ID：把绑定路径的 git common directory 归一化后登记；保留旧 project 名为 alias。仅当前 repo、已声明共享 roots 和有效继承关系可以参与，不保留「多个 descendant 歧义就搜全部」行为。
4. 非 git：realpath 作为 locator；每个不同 resolved path 独立，与 brief 一致。symlink 归一到同一路径。绑定中的显式 project 身份仍可覆盖这个默认值。
5. 首次显式初始化/写入时登记 UUID。纯读 `context/resolve/search` 遇到未登记且未绑定路径不写 registry：返回 `unregistered`、仅 global 和空 project，给出准确诊断。

一个明确的旧 CLI 兼容例外：`search/list` 显式传入 `--settings <file>`、没有 `--path/--project`、且该配置恰有一个非共享逻辑 project 时，视为选择这个独立记忆库，读取该 project + global。多个 project 绝不依此合并或全查。`context` 始终使用真实 path，不使用此捷径；文档日常 search 始终传 `--path "$PWD"`。这使旧单项目 CLI 用法与原格式测试保持兼容，而不保留歧义时扩展到所有项目的行为。

bare repo 使用其 canonical git directory；submodule 是独立 repo。不同 clone 默认独立：相同 remote 只提示可能关联，**不能自动合并**（fork、镜像、同 remote 不同用途都可能存在）。移动 repo 后 locator 变化，需要 `project attach --project <id> --path <new-path>` 显式重新关联；旧别名保留以便审计。该命令是未来新增契约，不是现有功能。

旧 bindings 如果曾把多个本地 repo 显式命名为同一 project，兼容期保留这一历史共享身份，并登记多个 locator；不推翻用户配置。相反，同一个 canonical repo 下原来有多个路径项目时，迁移合成一个目标 project，保留旧名称别名、原条目的来源和冲突标记，不合并/覆盖内容。若某 locator 同时要求两个不兼容的显式 project ID，视为身份冲突，拒绝该次切换，继续使用旧配置的精确绑定；不猜、不扩大 scope。解决的是项目级映射，绝不要求逐篇重分 523 篇。

### 2.3 启动预算与完整性

库内两份 MEMORY.md 使用以下完整索引预算。无参数 `brief` 是默认开场，只输出第 3 节规定的 2 KiB 摘要；显式 `context --path <actual-cwd>` 才输出固定包装说明、global 索引和当前 project 索引。**两者均为主动命令读取，不是注入，也不写用户指令文件。**

| 部分 | 最大物理行数 | 最大 UTF-8 字节数 |
|---|---:|---:|
| global/MEMORY.md | 50 | 8,192 |
| project/MEMORY.md | 150 | 24,576 |
| scope/路径/溢出/读取说明包装 | 20 | 2,048 |
| 整次 context stdout | 220 | 34,816 |

两个索引合计最多 200 行。这是本产品的明确预算，不伪称 Claude 原生也有这两个份额。包装单独计算；空 scope 不转让预算，不会因 global 膨胀挤掉 project。

每个索引条目一行，`- [Title](topics/name.md) — one-line hook`，约 150 个 Unicode 字符以内；硬上限 150 字符，同时服从文件字节预算。完整链接优先，缩短可读 hook/title，不截断 UTF-8、Markdown 链接或任意半行。若仅链接就超限，省略该条并增加 omitted 计数。标题转义方括号，路径正确编码。路径基准随 scope header 明示，普通 Read 可由基准和链接拼出绝对路径。

索引只路由、不写规则正文。按显式 pin、active topic、兼容 evidence 的顺序选择；同档按持久更新时间降序、稳定 ID 打破平局。已 superseded 的条目不进启动索引。所有被预算省略的可用条目仍可 FTS 查找。包装输出 `total/listed/omitted` 和 `truncated=true|false`；只要相关 scope 有 omitted，agent 不能把「索引没有」当成「没有记忆」。`context` 不调用网络、不做 FTS、不读全部 topic 正文进 stdout。

### 2.4 新记忆的放置与提升

- **默认 project**：当前 repo 的进行中事实、决定、期限、外部入口，以及作用边界仅在当前项目得到确认的反馈。
- **global**：用户明确跨项目的工作偏好/角色；明确要求适用于所有项目的反馈；确实共享且无项目限定的外部入口。`metadata.type=user` 并不自动意味着 global，例如某项目的用户角色仍留 project。
- 不确定适用范围就留 project；没有 project 时要求写入者指定 `--global` 或有效 `--project`，不能自动把临时目录中的内容写成全局。
- **project→global 只能显式提升**：依据用户「以后所有项目都……」之类已给出的指示，或显式维护决定。复现次数、年龄、搜索频率都不能自动扩大权限范围。已有授权足够时 agent 直接执行，不另造确认流程。
- 提升复制/提炼为新的 global topic，保留原 project topic、原 ID 和 `derived_from`；记录目标 ID 与来源。它不自动使原 topic `superseded`。可能包含项目专用内容时先形成适用于 global 的正文，不能整篇搬运。
- global 和 project 存在适用范围重叠的矛盾时同时提示；项目约束可细化通用规则，但不能默默抹掉用户明确的全局硬约束。无法判定就保留冲突供当前任务解决。

## 3. Q2：第一次调用有路可走，零命中不再是死胡同

### 3.1 硬裁决与问题定义

`CORRECTION.md` 作废了原 brief 的候选 (b)。**REJECT 所有向 CLAUDE.md / AGENTS.md / 用户文件写入索引或指令片段的方案，也 REJECT 生成文件让 runtime 自动加载的变体。** 这不是等待授权的功能，不留 install/adapter/hook 的实现接口或后续试验项。索引保存在 agent-memory 自有存储内，仅通过主动命令读取。

接受用户实测：8 个自然开场查询中，`conventions`、`记忆`、`gotchas` 零命中，得到 `(no results)`、exit 0 后 agent 放弃。`memory=3`、`how do I start=1`、`what should I know=3`、`setup=3`、`best practices=3` 是同次历史观察。不同安装版本可能用 `[]`、空 stdout 或空表呈现；它们属于同一个失败模式，不能用输出字符串差异否定实测。

「必然有用」在工程上落实为：**只要能访问库，第一次普通调用必定返回真实匹配或可执行导航；库空、scope 未确定、权限/DB 故障也返回具体状态和下一步。** 不承诺每个任务必有相关事实，不通过伪造命中满足指标。

### 3.2 ADOPT 无参数 `agent-memory brief`

保留现有安装入口发现：优先 `agent-memory`，否则 `python3 "$AM_SKILL_DIR/scripts/agent_memory.py"`，使用 argv 数组，不从 cwd 猜安装目录、不依赖 `CLAUDE_PLUGIN_ROOT`。开场调用不需要 agent 想关键词，也不需要提供 path：

```bash
"${AM[@]}" brief
```

`brief` 用真实 cwd/git common-dir 解析 project，读取 global + 当前 project 的索引投影，**不执行关键词搜索**。需要指定其他目录时支持 `brief --path <path>`；`context` 保留为显式展开完整索引的第二步，不与 brief 争夺开场入口。

brief/context 同时支持 `--project`、`--global`、`--no-shared`、重复 `--tag` 和原 settings 选择；互斥或矛盾的 scope 参数报错。用于 search 的后续导航时继承原 scope/filters/settings，只有初次无参数开场才默认 cwd + global。不能把受过滤的搜索导航成未过滤的全局摘要。

默认人/agent 可读输出示意（数值是示例，不是实测结果）：

```text
status: ready   project: toolbox (prj_…)   source: markdown-index
visible: 123 distinct memories; global: 12; project: 116; overlap: 5
GLOBAL 2/12
- [偏好简短进度](<absolute-readable-path>) — 汇报进度时使用
PROJECT 4/116
- [当前发布决定](<absolute-readable-path>) — 本轮发布边界
TAGS: release(8), cli(6), workflow(4)
omitted: 117; shown entries are navigation, not search matches
NEXT: agent-memory context --path '<actual-cwd>'
```

project/global 的分项不能相加当 union 总数；底层按物理文档/稳定 ID 去重。每个条目给普通 Read 可用的完整路径或明确基准+完整相对路径；长绝对路径改用单个 root alias，不能断链。最多展示 global 2 条、project 4 条、当前 scope 的 5 个 tags；具体数量受预算进一步裁剪。无当前 project 时仍有 global 摘要和项目选择导航。

**开场及零命中导航的硬上限：整份输出 UTF-8 ≤2,048 bytes；token 预算为 2,048。** 实现不依赖在线 tokenizer，保守地按每 UTF-8 字节消耗一个 token 预算单位；对字节级 tokenizer，正文 token 数不会高于这一上界。不同 runtime 的封装/工具调用额外 tokens 不受 CLI 控制，不声称跨 runtime 精确总 token 数。验收以整份序列化输出的字节硬上限为必过标准；若使用特定 tokenizer 做附加测量，记录名称/版本，不用估计值冒充精确上限。

预算包括 status、scope、count、links、tags、omitted、next commands 和 JSON escaping；最多一条主 next command、一条备选。裁剪顺序为次要 tag→后排 project/global 条目→长 hook，必须保留状态、scope、准确或显式 unknown 的 count、omitted 和至少一条可执行 next command。极长 cwd 不塞入命令，用无参数 `brief/context` 或短稳定 project ID；极长 topic path 无法完整放下则省略该条，给展开命令。空库给 `empty_scope` 和 `capture --help`；无法读取给 `unavailable`/诊断与 `status`/`doctor`，不能把未知 count 写成 0。

长 query/诊断只显示有界 preview 并标 `query_truncated`/`diagnostic_truncated`，query 另附原 UTF-8 长度与 SHA256；不改变实际执行的原 query。若完整入口或 settings 路径本身超过预算，next action 使用「继承本次入口/settings + 子命令 argv」结构，不丢 settings 来缩短：envelope 显式 `inherit_invocation=true`，调用方在已知原 argv 上替换子命令；SKILL 的 `AM` 数组保存入口及公共 settings 参数，文本用 `"${AM[@]}" context ...`。终端展示同样的复用说明，不能把未定义变量伪装成独立可执行命令。验收按原调用 argv 重建 next action 并执行，所有路径/参数仍按 argv 传递。

MEMORY/cache 缺失或过期时从可访问 Markdown 元数据在内存生成摘要，不要求预先写 cache；没有 DB 也可读索引。普通 brief、context、零命中兜底均只读，显式 refresh/sync 才更新 agent-memory 自有缓存。

### 3.3 ADOPT 零命中导航包；不扩大 scope 冒充命中

在 CLI 呈现层处理空结果，底层 `search_documents()` 仍返回 `[]`，不改现有词法/CJK/RRF 排序。已有 AND→OR 放宽保留，并沿用 `match_mode` / matched/missing terms 标注；本轮不再加一层自行编造同义词或删限制条件的自动放宽。

普通 search 最终无匹配时返回：

- `status=no_match`、query 或显式截断的 query_preview、已使用的 scope 与实际 match mode；`results=[]` 保持真实。
- 当前 project + global 的 distinct 总数及分项；scope 内最多 5 个有计数的 tags。
- 同 scope 的 global/project 短索引兜底，标记 `navigation`，不能放进 results 或算作命中。
- 一条优先下一步：零命中已经附了摘要，固定建议保留过滤的 `context` 展开，或有确定 tag 时 `list --tag ... --limit 5`，或 Read 某个已展示 topic；空库/不可用使用前述专门出口。CLI 无会话状态，不猜是否以前 brief 过；SKILL 决定上下文丢失时是否再读 brief。命令使用当前实际入口与正确 scope，argv/路径可靠转义。
- 无法确定 project 时列最多 3 个已配置项目的名称/ID供选择（不含其它项目正文、tags 或文档计数），附剩余项目数和显式 `projects` 导航。已确定 project 不主动列其它项目内容。

对 `--global`、显式 project、`--no-shared` 等过滤保持原可见边界；既不能零命中自动跨项目，也不能给原来排除的 global 内容。零命中退出码仍为 0（查询成功但没有匹配）；权限、解析或 DB 损坏保持非零，输出不同诊断，绝不误报为 no_match。缺索引时可附文件索引导航，但不掩盖检索失败。

默认 agent-facing text/table/YAML 输出必须有上述有界出口，不再只有 `(no results)`/空表/空列表。为保留机器消费者，旧显式 `search --json` 的 stdout 数组合同保持：无命中为 `[]`，同时向 stderr 输出有界导航；两路合计遵守 2 KiB。新增 `--envelope` 输出单一结构化 JSON 对象，字段为 `schema_version/status/query/scope/counts/results/navigation/next_commands/truncated`，空匹配也完整可处理。文档要求自动化新消费者用 envelope；明确只采 stdout 的 legacy JSON 客户端不具备导航保证，不能把这个兼容例外写成所有调用都已闭环。

正命中默认仍保持已验收紧凑结果格式与原顺序，不每次额外附整份索引，不抵消既有约 76% token 节省。envelope 是显式新格式；导航预算不用于悄悄裁剪正常匹配的旧输出。

### 3.4 当前任务上下文自动构造查询

**ADOPT cwd/git identity 自动选 scope**，这是 brief 和 search 默认行为，不把 repo 名称当成必须命中的关键词。**ADOPT 显式 `search --from-context` 的确定性辅助查询，安排 P3**：读取 `git diff --name-only HEAD`（unborn repo 分别读取可用 staged/unstaged 名称），最多选择 10 个改动文件的 path/basename；错误串只能来自调用者明确提供的 `--error`，任务文本只能来自 `--task`，CLI 没有读取宿主对话的能力。默认不读取 diff 内容、shell 历史、环境秘密或启动后台 watcher。

从显式错误串、路径/标识符、可用任务词依次构造至多 3 条短查询（单条 UTF-8 ≤256 bytes），返回所用 query/source 与 match mode，不让生成过程不可见。使用现有检索函数；多查询候选按确定性合并并保留 provenance，仅影响这个新 opt-in 模式，普通 search 与 gate 题不变。无 diff/无输入时直接返回 brief 导航；辅助查询全落空时返回同一 no_match 导航包，不无限改写、重试。

不能保证改动文件就是任务目标，故**REJECT 默认开场自动读 diff 后猜关键词再搜**。**DEFER LLM 查询扩展、语义改写与自动 rerank**，直到有剩余失败任务集和实测收益；修复死胡同不依赖这些收益成立。

### 3.5 SKILL.md 的消费协议与 runtime 差异

保留已有效的 description 触发语义，不靠追加触发词解决失败。正文明确要求：

1. 任务开始执行一次无参数 `brief`，先看实际 scope/count/index，不先猜 `conventions` 等泛词。
2. 按 hook 用普通 Read 读相关 topic；需要更多路由时 `context`，需要定点证据时 search。
3. 一次零命中只代表该 query 无匹配。读返回的 navigation/count/tags，并执行最相关的一条 next command；不能把 no_match 总结成「没有记忆」。
4. 如果摘要、展开索引或一次有依据的替代查询仍无相关条目，明确「当前可见范围未找到相关记忆」后继续任务，不无限搜索。库真空或不可用时按准确状态处理。
5. 项目切换、压缩后索引不可见时重新 brief；global 提升仍显式，搜索命中不自动授权写全局。

三个 agent 的实际共同点是已有 skill 被触发后可主动执行同一 CLI，再用各自普通文件读取工具；没有新的 native-file 加载步骤。Claude 原生 per-repo memory 继续独立存在；Codex/OpenCode 不需要任何指令文件改动；裸终端直接得到相同有界 stdout/navigation，调用者自行阅读或转交 agent。CLI 不能强迫模型遵守协议，也不保证压缩后常驻。第一调用的可执行输出可以机器保证，后续 agent 是否真的继续 Read 需要真实会话验证。

旧 archive 原文可能含旧绝对路径或跨 root 相对链接；为保持字节不变，使用现有 `links <id-or-path>` 的兼容解析取得新路径再 Read。新 topic 链接应直接可读。导航明确区分这两种路径，不声称 legacy 文件脱离 resolver 也全能打开。

## 4. Q3：523 篇与 bindings 的迁移

### 4.1 不做批量语义猜测

迁移首先解决物理位置和身份，不冒充内容审校。523 是本次实测语料数量，工具实现不能硬编码数量。旧正文、frontmatter 原文、原 source hash、原 ID、scope 集、有效 tags、相对/wiki/`memory://` 链接关系全部进入核验清单。

有合法 `mem_...` 的保持不变；缺失 ID 通过迁移 manifest 持久分配一次，重试不变，不就地运行旧 `migrate_ids.py` 去破坏回滚基线。重复 ID 若源文件确为同一物理文档则去重引用；不同内容同 ID 不静默挑一个，报告冲突并保留旧 store，等待项目级修复。历史同名/同标题不等于同文档。

旧 `learning/drawback/error/feature-request` 可以确定性读成 `event_kind`，不能确定性变成四种 `metadata.type`。无充分依据的条目继续作为 `legacy_evidence`，无强制伪造的 type；可被索引路由、搜索、阅读，显示「历史证据，未复核」。明确属于排除项的历史代码/git/调试材料只保留 archive 和按需搜索，不继续生成同类新记忆。迁移不试图自动语义识别全部排除项，也不删除历史内容。

无需用户重分 523 篇：全部立即保有原检索能力；后续读取到某条、用户纠正某条、或显式维护时，agent 可依据当前证据提炼出新 topic 并链接旧 evidence。未复核条目可以永久留 archive，并不阻塞系统使用。

### 4.2 可恢复事务

以下均为待实现命令契约：

```bash
"${AM[@]}" migrate layout --dry-run --output /tmp/am-layout-plan.json
"${AM[@]}" migrate layout --apply /tmp/am-layout-plan.json
"${AM[@]}" migrate layout --rollback <migration-id>
```

1. **盘点**：读取 v1 settings 全部嵌套 bindings/shared/inherit_memory，相对路径按旧基准解析；按物理源去重，记录多 scope 归属、有效 tags、旧 alias、git locator。先生成计划，列出无法访问的路径、身份冲突、重复 ID、坏链接。
2. **备份与锁**：迁移锁拒绝同时运行另一个迁移；阻止受本 CLI 管理的 capture/lifecycle/sync 写入。SQLite 用 backup API 或一致性快照，不能只复制 main DB 而忽略 WAL。现有 snapshot 的 `missing_roots/unreadable_roots/skipped_files` 必须全部为空；否则不切换。
3. **staging**：在目标文件系统构建独立 generation（实现可用 `generations/<migration-id>/...`，活动 root 由 settings 原子指向）；旧 roots 一字不改、不删除。archive 保留原目录相对结构和原 Markdown 字节；跨 root 链接用 manifest 的原路径→新路径解析，不通过强行重写原文改变 hash。新导出 topic 才改写链接，原文件留作 provenance。
4. **关系与身份核验**：每个原 source 都有目标；hash 全等；所有旧 ID/旧路径 alias 可读；迁移前后有效 scope 集和 tags 相同（canonical repo 合并的旧项目通过 alias 投影比较）；链接解析到同一逻辑 memory。原本已坏的链接记录为既有问题，新坏链接数量必须为 0。
5. **索引与行为核验**：只在 staging 建 DB；legacy 双读解析、scope 过滤、CJK/RRF、返回路径到稳定 ID 的映射通过验证。523 条样本保有 523 个原来源实体，不因共享 root 被重复计数。不把 MEMORY.md 算入 corpus。
6. **切换前复验**：检查 dry-run 的 settings/hash 与当前源仍一致。编辑器或旧进程不遵守本 CLI 锁，仍可能写原文，因此不能只依赖锁；切换前发现变化就中止，保留原 store 可用，重新生成计划。生成完整 journal 后原子替换 settings 的 active generation 指针。
7. **失败/崩溃恢复**：切换前失败保持旧 store；切换后首次校验失败，依据 journal 原子恢复原 settings/registry 指针。重复 apply 根据 migration-id 返回 `already-applied`，不得再复制或分配 ID。目标磁盘满、权限错误、SQLite 不可用都不能删旧文档。

### 4.3 回滚与兼容期

回滚不仅是删新 DB。必须能恢复完整 settings、registry、旧 roots 和 DB 一致性。切换后若尚无新写入，直接回切旧指针；若已有新 capture/修改，rollback 先将新 generation 全部快照并制作 delta bundle，保存 topic、scope 和 ID，输出其位置，再回切。新写入不会丢，但旧版本未必直接识别新 schema；恢复新 generation 或显式 replay 才重新可见。这一限制必须写进命令结果。不能把「旧版本看不到新 topic」伪称完全透明回滚。

兼容承诺：P1 保持完整 v1，P2/P3 同时读取 v1 与 v2；P2 默认只 dry-run，用户执行 apply 才搬迁。三个发布版本都保留原 `capture <kind> <summary>` 位置参数形式，在 v1 store 上仍写原目录/格式；v2 新增 `--memory-type`，旧 kind 位置参数只提供 event_kind，缺乏语义 type 时不猜测。旧 project 名、路径和 memory ID 在兼容 resolver 中可访问。

退役旧写入格式仅在最后一个兼容版本发布满 90 天、真实库验证通过、没有未处理迁移 blocker 时考虑，另发退役决定；不按时间自动删备份/alias，也不自动撤销旧读能力。迁移失败仍使用旧路径+精确 bindings+global 的可用行为，不退到全项目搜索。失效 bindings 可继续显式 `--project` 读健康 roots；无法判定当前 project 时仅 global 并报诊断。写入未能确定目标时非零退出。

## 5. Q4：FTS5 是索引不足时的补充

**默认主路径固定为无参数 brief → 相关 hook → Read，必要时 context 展开更多索引。** 取消当前 SKILL「所有非平凡任务一开始必须 search」的规定，保留检索能力及其现有质量，不把每轮任务变成 query 构造练习。

以下任一情况触发 search：

1. 当前问题明确需要历史记忆，但 brief/展开索引没有相关 hook；相关 scope 有 omitted 时不能仅凭摘要没显示就断言没有记忆。
2. 有精确 memory ID、错误串、命令、路径/标识符要查，或者需要历史证据来核实/解释冲突。
3. 读到 topic 后仍需其来源/相关记忆，无法通过明确链接解决。
4. 用户明确要求跨项目寻找经验；此时使用显式 `--all-projects` 或指定 project 集。

普通 search 默认只检索当前 project + global（显式单项目 settings 的兼容选择见 2.2）；`--global` 仅 global；无绑定/身份歧义不放大到全部项目。global 表示共享层，绝不是「所有项目」。`--all-projects` 的结果携带来源 scope，不把命中自动复制或提升到 global。读取文件失败、索引缺失或损坏给出诊断与明确 sync 指令；context/Read 仍可用，search 不在后台偷偷重建数据库。

任何检索零命中都执行第 3 节同 scope 导航合同；不能把 fallback topic 当作真实搜索结果或给 gate 加假命中。

保留当前词法 FTS5、CJK bigram 辅助索引、RRF 和紧凑结果格式。schema/路径改造不得改变受保护 query 的排序，legacy evidence 默认仍可搜索。新 active topic 与旧 archive 命中都附 type/status/scope，agent 读取后判适用性。superseded 不进启动索引，但兼容搜索仍能找其证据并显式标状态；不能为了「canonical 优先」一次性隐藏旧语料造成既有 gate 回退。

**DEFER canonical 加权、统一隐藏历史结果、时间衰减、BM25 字段调权、向量/LLM rerank。** 只有有标注的「同主题当前规则 vs 历史证据」评测集、现有 gate 全通过、证明实际任务有收益且成本可接受时才重开；年龄本身永远不触发物理删除。本轮不承诺任何新的 recall/token/latency 百分比收益。

## 6. Q5：格式与生命周期

### 6.1 新 topic 的规范格式

```markdown
---
name: prefer-brief-status-updates
description: 汇报执行进度时使用简短事实句，方便用户快速判断下一步
metadata:
  type: feedback
  agent_memory:
    schema_version: 2
    id: mem_<preserved-or-new-uuid>
    event_kind: learning
    status: validated
    derived_from: []
---

进度汇报只写已完成事实、当前阻碍和下一步。

**Why:** 用户明确纠正了冗长、重复的进度播报。

**How to apply:** 每次给出一小段；只有需要比较时使用列表。

相关记忆：[[another-memory-name]]
```

`metadata.type` 是唯一新语义分类；`event_kind` 可省略，表示历史事件来源，不是它的替代。`name` 为同 scope 唯一 slug，文件 basename 与之相同；`description` 为具体的相关性判断钩子。`feedback/project` 正文采用 rule/fact、`**Why:**`、`**How to apply:**`；user/reference 不强加无意义模板。用户偏好、纠正、不可由代码/git 推导的项目事实、外部资料位置可写；代码模式、git 历史、调试配方、CLAUDE.md 已有内容不作为新 topic。

扩展字段放 `metadata.agent_memory`，保留原 id、status、tags、时间与 provenance 等已有信息；导入时保留未知 metadata 字段，更新不能破坏它们。升级 parser/writer 必须支持嵌套 YAML、明确报错而不半解析；不得用当前平面正则删除或改写 `type`。本轮固定为 Python 标准库实现的受限、保留原文跨度的 frontmatter parser：支持缩进 mapping、scalar、引号字符串、标量列表及当前 v1 已支持形式；未知的已支持结构原样保留。拒绝重复 key、anchor/alias、tag 或不支持的 YAML 构造，报具体位置且整份文件保持不变。不声称支持完整 YAML，也不增加 pip 依赖；导入特殊 YAML 必须先显式转换成支持子集，原文件留存。

旧顶层 `type` 只按 v1 event kind 理解。双读结果提供 `memory_type: null` 与 `event_kind: error` 等明确区分；禁止将平面 `type: error` 当作 Claude type。旧未知字段、原文、Logged 时间等不丢失。新写入用 v2，旧 writer 仅服务未切换的 v1 store。

兼容边界必须写进代码接口：现有 `capture_memory(binding, kind, ...)` 的 v1 路径继续输出 `learnings/` 等目录、顶层 type 及原正文标题；v2 使用明确 schema 分支，不改旧函数调用的隐含格式。旧 lifecycle 允许指向存在的 raw legacy evidence，原测试因此保持；只有 v2「提炼为当前 topic」操作要求目标是有效 typed topic。目标存在与防环检查可统一增强，但不能把合法旧 raw→raw 关联偷偷改成错误。底层无 scope 的库查询函数可保留供 benchmark/显式内部调用使用，公共 CLI 负责上述严格 scope，不向用户暴露隐式全库回退。

互通承诺分两层：基础导出只含 Claude 三个基础字段、正文和可解析链接；扩展 metadata 是否被 Claude 原生无损保留尚无实测，不承诺。无 type 的 legacy evidence 不伪装成合法 Claude topic；仍可原样导出 archive。导入只读用户明确给出的源路径，不依赖 Claude memory 目录扁平化编码；导出只生成 agent-memory 自有 `exports/` 下的便携包，后续由用户自行使用，不写任何原生自动加载目录或用户指令文件。迁移与导出重写路径时记录映射、保留原 ID provenance。

### 6.2 让已有生命周期进入日常回路

- `raw`：未经确认的候选/观察，不当作确定事实；capture 输出该状态及 Read 路径。
- `validated`：用户直接确认，或有明确可记录证据；新反馈可直接 validated，不必先制造一次 raw。不是检索命中次数越多就越可靠。
- `promoted`：旧 evidence 已有对应的提炼 topic，`promoted_to` 必须是存在且可读取的目标 ID，自动建立来源链接。它不是「已经变成 global」。
- `superseded`：被显式新结论替代，`superseded_by` 必须存在；拒绝自环/环，旧正文保留，索引不再把它作为当前路由。
- scope 提升另用 `promote --to global --id ...`，创建新目标、保留 source；lifecycle 状态不能隐式决定 scope。

日常 trigger：agent 读到 legacy/raw 后，本次任务得到用户确认才提炼；用户纠正时写新结论并关联旧条目；每次 capture/lifecycle 都返回状态、目标是否有效和必要下一步，不让字段只躺在 YAML。已有授权内完成，不要求用户逐项审批全部 corpus。批量 maintain 先输出 proposal/diff，只有显式 apply 才修改；其自动调度留待后续信号。

**ADOPT evidence/topic 的用途分离及 provenance；DEFER `memory_role=semantic/episodic/procedural` 第三套枚举。** 当前 `metadata.type + legacy_evidence/active topic + status` 已能表达必要决策。只有真实消费方需要且 type/status 无法表达的查询或维护动作出现时，才增加 role，避免研究分类变成必填字段。

## 7. 候选方案逐项裁决

| 候选 | 裁决 | 理由 / DEFER 重开信号 |
|---|---|---|
| 仅 Claude per-repo 或仅 `_shared` 一层 | REJECT | 分别缺全局层或项目隔离。 |
| global + stable project + Markdown + SQLite | ADOPT | 满足三项硬约束，保留现有底座。 |
| 照抄 Claude 路径扁平化；hash 当前 worktree root | REJECT | 不稳定接口；worktree 会被错误拆开。 |
| 持久 UUID + canonical git common-dir + 显式 attach | ADOPT | 同 repo 共用，移动/跨 clone 有清晰显式路径。 |
| remote 相同自动合并项目 | REJECT | 不能证明是同一记忆权限/用途范围。 |
| index→Read 为主、FTS 为补充 | ADOPT | 对齐一手事实，同时保留超预算和跨项目搜索。 |
| 全任务 search-first；删除 FTS 来完全仿制 Claude | REJECT | 前者偏离使用方式，后者丢掉已有结构性能力。 |
| CLI 自称自动注入/永久常驻 | REJECT | 不具备宿主上下文控制权。 |
| 无参数 brief + 零命中同 scope 导航 | ADOPT | 不猜关键词也能获得索引、库存与下一步；空结果不再死路。 |
| 向 CLAUDE.md/AGENTS.md/用户文件写索引或指令；生成 runtime 自动加载文件 | REJECT | CORRECTION 明确禁止；包括短片段、adapter、import 等变体。 |
| 为自动加载生成 hook/import/launcher 文件 | REJECT | 同属用户否决的宿主加载方案，不以技术验证或再请求授权重开。 |
| 额外自动放宽关键词或 scope | REJECT | 容易伪造相关性/泄漏项目；保留已有且标注的 AND→OR，最终无命中给导航。 |
| 可见 project/tag 清单、distinct count、索引摘要、下一步命令 | ADOPT | 同 scope 有界输出，导航与真实匹配分离。 |
| 默认用改动文件猜任务并自动检索 | REJECT | diff 未必代表当前任务；无参数开场只做索引导航。 |
| 显式 --from-context 的路径/错误串辅助查询 | ADOPT | P3 确定性、有限次数、可解释；零命中仍回到导航。 |
| LLM 查询扩展 | DEFER | 确定性导航后仍有标注失败集，且实测收益支持时重开。 |
| 自动从 project 升 global | REJECT | 证据复用不能自行扩大适用范围。 |
| 四种 Claude type + 独立 event_kind + 旧格式双读 | ADOPT | 新格式对齐，旧历史不被错误分类。 |
| 将四种旧 kind 一对一映射成四种 Claude type | REJECT | 来源类别与记忆语义不是同一轴。 |
| 523 篇人工重分或批量 LLM 改写 | REJECT | 无必要且有失真风险；无损兼容与按需提炼即可。 |
| evidence/topic 分离、目标验证、显式生命周期 | ADOPT | 使用既有字段形成最小闭环，不删除来源。 |
| memory_role 第三枚举 | DEFER | 有无法由 type/status 表达的实际消费者时重开。 |
| canonical/时间/字段权重重排 | DEFER | 有对应标注题、保护 gate 全绿、实测收益后重开。 |
| embeddings、图数据库、LLM 自动 consolidation | DEFER | 索引/FTS 有可复现的剩余失败集，且本地成本与增益测量支持时重开。 |
| 按年龄删除或自动定时覆盖原事实 | REJECT | 年龄不等于失效，破坏 provenance 和可回滚性。 |
| 自动 maintain 提醒/调度 | DEFER | 显式维护使用证明有效且积压可量化，再确定触发阈值；本轮不编造 N 天/N 条默认值。 |

## 8. Q6：MVE-first 发布分期

每期都是可独立验收、可发布的增量；后一期可依赖已发布前一期，但前一期不能依赖未实现后一期才能工作。所有期共用第 10 节保护门禁。

### P1 — 可用的跨 agent 读取回路，不搬库

交付无参数 brief、全部 agent-facing 零命中导航、结构化 envelope、repo identity resolver、严格 scope、按需 context、SKILL 的 brief-first/失败后继续规则；保持现有 description 触发语义与 bootstrap。在 v1 store 上按 bindings/shared 生成 `context-cache` 路由投影，链接仍指向原 Markdown，明确 legacy evidence；新 repo locator 与旧 bindings 做别名关联，使根/子目录/worktree 同 scope。已存在且新鲜的索引投影直接读；cache 缺失或源文件集合/mtime/size 变化时在内存重建路由投影并输出，首次使用无需预先写 cache。显式 `context --refresh` 才持久化 cache，普通 brief/context/零命中兜底不写 DB/源文档。新鲜度检查只优化投影，不能决定迁移 hash 等安全核验。

验收：原 37 测试 + identity/brief/context/navigation/bootstrap 新测试；原 retrieval gate PASS；8 个实测开场 query 全部得到真命中或可执行导航，dead-end=0；同 repo 三种 cwd 得到同 project，坏路径不全库搜索；2 KiB 首屏/兜底、完整索引预算、链接、read-only 均通过。CLAUDE.md/AGENTS.md/用户 runtime 配置目录的文件清单与字节不变。CLI 不可用时给出可执行的 Python fallback。此期保留旧 capture，不等待迁移或新 type 就可修复失败。回滚恢复程序版本与 registry/settings 备份，删除自有派生 cache 即可，原 523 文件字节完全不变。

### P2 — v2 topic 与可回滚布局迁移

交付新库布局、新 topic schema、v1/v2 双读、无损 archive、完整 dry-run/apply/rollback、旧 alias 与链接解析；新建库直接 v2，已有库仅显式 apply 切换。新 topic 支持从普通文本编辑器直接写文件，然后显式 sync；不把 CLI capture 变成唯一写入口。reader 可直接读 Markdown，无 DB 仍可用索引。

验收：P1 全部 + schema/migration/link/storage 测试；523 来源的 hash/ID/scope/tag/link 对照全过；注入各阶段故障不丢原文件；切换后新写入回滚保留 delta；删除 staging DB 后重建结果一致；原 corpus gate 仍 PASS，另测新布局等价。P2 不需要调权、向量或自动批量整理就可发布。

### P3 — 显式提炼、纠正与互通

交付 lifecycle 目标校验、防环、read/capture 后状态提示、显式 project→global、Claude 基础格式的便携导入导出、显式 --from-context 辅助查询。实现按需提炼，而不是扫描 523 篇要求人工确认。索引排除 superseded、显示冲突与 provenance，搜索维持兼容证据可见性及原排序基线。

验收：P2 全部 + lifecycle/promotion/interop/context-query 测试；不存在目标拒绝且源文件不变；global 提升只因显式操作发生；导入导出基础字段/正文/链接可往返；原 evidence 和 ID 不丢。辅助查询来源可追踪、最多 3 条、普通搜索排序不变、全落空有导航；导出不写用户原生加载文件，只声明便携格式兼容，不宣称自动安装到原生 memory。回滚使用 generation 快照+新写入 delta。

其余 DEFER 项不混入这三期，也不把已完成 CJK/bootstrap/compact 输出列作新增交付。

## 9. 五条并行 lane：唯一文件写权限与接口

以下白名单是**未来实现**权限，本次仍只写本计划。所有路径相对 repo root；未列出的文件默认冻结，尤其 `memory_cjk.py`、`package.json`、原 37 测试、原 retrieval 四个文件及 judgments。不能为消除冲突跨 lane 改文件；需求交给该文件 owner。`PLAN-ARCH.md` 不分给实施 lane。

下述 lane 命令描述完整交付的验收。分期由 `arch-contracts.json` 逐 case 标记 `introduced_in: P1|P2|P3`：P1 必需 identity/brief/context/navigation/bootstrap 与 scope/storage/integration 的 P1 case；P2 累加 schema/migration/新布局检索 case；P3 再加 lifecycle/promotion/interop/context-query case。未实现的后期测试不提前合入该期发布，不得把当期必需 case 标成 skip 来通过。每条 lane 除 unittest 命令外，都必须运行 `python3 -B eval/agent-memory/verify_arch.py --lane A --phase P1`（换成实际 lane/phase），要求 exit 0、`status="PASS"`、`failures=[]`，且实际执行的当期必需 case ID 集与 contracts 完全相同、数量大于 0。缺文件、0 tests、缺 case 或 skip 必需 case 都失败；只有 unittest 的 `OK` 不算验收。

此规则仅适用于当期参与 lane：P1 为 A/C/D/E，B 在 P1 没有交付、不执行其 lane verifier、不记为通过；P2/P3 为 A/B/C/D/E（包括既有功能回归）。contracts 显式列参与表，不能用缺测试文件把参与 lane 自动当作不参与。

### A — 身份、配置与迁移

唯一写权限：

```text
skills/agent-memory/scripts/memory_config.py
skills/agent-memory/scripts/memory_admin.py
skills/agent-memory/scripts/memory_snapshot.py
skills/agent-memory/scripts/memory_identity.py                 (new)
skills/agent-memory/scripts/memory_migrate.py                  (new)
eval/agent-memory/tests/test_arch_identity.py                 (new)
eval/agent-memory/tests/test_arch_migration.py                (new)
```

验收命令：`python3 -B -m unittest discover -s eval/agent-memory/tests -p 'test_arch_identity.py'` 与同目录 `-p 'test_arch_migration.py'`，均 exit 0、`OK`。必须覆盖真实临时 git repo 根/子目录/linked worktree/symlink、非 git、两个相同 remote 的 clone、移动 attach、v1 nested/inherit、多 scope 共享 root、ID 冲突、dry-run 不写源、重复 apply、每个事务阶段故障、切换后写入回滚保留 delta。scope 泄漏为 0，迁移新坏链接为 0，原 source hash 全等。

### B — topic schema、capture 与生命周期

唯一写权限：

```text
skills/agent-memory/scripts/memory_schema.py                  (new)
skills/agent-memory/scripts/memory_capture.py
skills/agent-memory/scripts/memory_lifecycle.py
skills/agent-memory/scripts/migrate_ids.py
eval/agent-memory/tests/test_arch_schema.py                  (new)
eval/agent-memory/tests/test_arch_lifecycle.py               (new)
```

验收命令：同一 unittest discover 分别 `-p 'test_arch_schema.py'`、`-p 'test_arch_lifecycle.py'`，均 exit 0、`OK`。覆盖四 type、未知 metadata 保留、旧 kind 双读、嵌套 YAML 更新不误改字段、523 不被要求分类、缺 ID 可重复映射、排除内容写入规则、feedback/project 模板、promotion 与 scope 区分、坏目标/环拒绝且文件字节不变、基础导入导出往返。语义写入规则用明确带类型/来源的 fixture 测，不声称纯 parser 能判断所有自然语言内容。

### C — 存储、检索与诊断

唯一写权限：

```text
skills/agent-memory/scripts/memory_store.py
skills/agent-memory/scripts/memory_store_ext.py
skills/agent-memory/scripts/memory_doctor_ext.py
eval/agent-memory/tests/test_arch_storage.py                 (new)
eval/agent-memory/tests/test_arch_retrieval.py               (new)
eval/agent-memory/tests/test_arch_navigation_inventory.py    (new)
```

验收命令：同一 unittest discover 分别 `-p 'test_arch_storage.py'`、`-p 'test_arch_retrieval.py'`、`-p 'test_arch_navigation_inventory.py'`，均 exit 0、`OK`；另必须执行第 10 节 measure + gate + frozen-input verifier，输出 `PASS`。覆盖索引排除 MEMORY/backup/cache、多 scope 不重复记录、只读 WAL 行为不回退、旧 `memory://` 与搬迁后的相对/wiki link、新库重建、scope 默认不全库、旧/新 corpus 等价排序。新增导航库存验证 scope union distinct count、跨 scope 不泄漏、tags 限额、unknown 与 0 分开；正命中 IDs/order/scores 不变。不得调整 CJK/RRF 参数或保护 gate 来掩盖失败。

### D — CLI、brief/context、零命中导航与 bootstrap

唯一写权限：

```text
skills/agent-memory/scripts/agent_memory.py
skills/agent-memory/scripts/memory_format.py
skills/agent-memory/scripts/setup.sh
skills/agent-memory/scripts/memory_context.py                 (new)
skills/agent-memory/scripts/memory_navigation.py              (new)
skills/agent-memory/scripts/memory_context_query.py           (new)
eval/agent-memory/tests/test_arch_context.py                 (new)
eval/agent-memory/tests/test_arch_bootstrap.py               (new)
eval/agent-memory/tests/test_arch_brief.py                   (new)
eval/agent-memory/tests/test_arch_navigation.py              (new)
eval/agent-memory/tests/test_arch_context_query.py           (new)
```

验收命令：同一 unittest discover 分别 `-p 'test_arch_context.py'`、`-p 'test_arch_bootstrap.py'`、`-p 'test_arch_brief.py'`、`-p 'test_arch_navigation.py'`、`-p 'test_arch_context_query.py'`，均 exit 0、`OK`（按本节分期 case 选择）。覆盖无参数开场、2 KiB 整份输出预算（含 stderr/JSON escaping）、超过 2 KiB 的 query/诊断及控制字符、完整链接、empty/unregistered/unavailable 区分、长路径/中文、cache 缺失/过期、只读 HOME/DB；所有输出模式的空结果出口与 legacy JSON stdout 数组兼容；navigation 不进入 results。无 `CLAUDE_PLUGIN_ROOT`、任意 cwd、空格安装路径、CLI/Python 两种入口均可用。快照确认读取前后用户指令文件、runtime 配置、源 Markdown 全部未变且无 DDL；正命中紧凑输出保留。P3 验证辅助 query 来源、最多 10 个路径/3 条查询、无 diff 和全落空出口、无无限重试。

### E — 使用协议、文档与跨 lane 验收

唯一写权限：

```text
skills/agent-memory/SKILL.md
skills/agent-memory/references/browse-and-tags.md
skills/agent-memory/references/doctor-sync-settings.md
skills/agent-memory/references/lifecycle-snapshot-links.md
docs/agent-memory.md
docs/agent-memory-doctor.md
docs/agent-memory-self-improvement.md
eval/agent-memory/tests/test_arch_integration.py             (new)
eval/agent-memory/evals/arch-contracts.json                  (new)
eval/agent-memory/evals/opening-queries.json                (new)
eval/agent-memory/evals/runtime-recall.json                 (new)
eval/agent-memory/verify_arch.py                             (new)
```

以上 docs 是正式使用/维护参考，不是工作笔记。可复现输入和 metadata 只进 `eval/agent-memory/evals/`；运行日志、before/after/gate JSON 和会话记录全部放 repo 外。

验收命令：`python3 -B -m unittest discover -s eval/agent-memory/tests -p 'test_arch_integration.py'` → exit 0、`OK`；`python3 -B eval/agent-memory/verify_arch.py --phase P1 --output /tmp/agent-memory-arch-acceptance/P1.json`（随后 P2/P3）→ exit 0，JSON `status="PASS"`、`failures=[]`、全部必需 case 执行数匹配 contracts。核验没有生成或修改用户指令文件的实现路径，brief-first/零命中继续协议与文档一致，CLI 文档例子真实可运行。自然语言提示的真实遵循率不能只用静态文本测试代替。

新增 `opening-queries.json` 固定八个原查询：`memory`、`how do I start`、`conventions`、`what should I know`、`setup`、`记忆`、`best practices`、`gotchas`。保存 correction 的观测次数作为 provenance，不把缺少原 scope/settings/limit 的历史次数当成跨环境固定期望。机器指标为 `actionable=8/8`、`dead_end=0`；合成 fixture 另强制八题全零命中，验证 results 仍空、navigation 有可执行下一步。每条 next command 在相同临时 cwd/settings 中真实执行，必须成功并保持 scope/预算；权限错误 fixture 则要求准确非零诊断，不能为了成功计数吞错误。

真实 runtime 探针契约 `verify_arch.py --runtime <claude-code|codex|opencode> --session-report <path>`：只使用已安装 skill 和独立的 agent-memory fixture，不改任何用户指令/自动加载文件。一组任务检查主动 brief→Read；另一组先给必定零命中的 query，检查收到导航后是否执行展开/Read/一次有依据的新 query，且最终引用 fixture 中唯一 topic nonce。报告包含 runtime/version、CLI 调用、scope、真实 results/navigation、后续动作和结果；来自真实会话而非手写。压缩恢复用另一个 case 检查再次 brief。无 runtime/凭证返回 `UNVERIFIED`，不能伪装 PASS；不阻塞 CLI 行为合同的发布，只限制「该 runtime 已实测遵守后续动作」的声明。终端探针直接验证 CLI 输出、下一步可执行性和 exit code，不要求模型行为。

### 并行前固定的接口与集成顺序

以下契约由本计划冻结，实施 lane 不需争抢公共文件：

- A：`resolve_context(settings, path, explicit_project=None)` 返回 `project_id/status/aliases/global_roots/project_roots/diagnostics`；status 为 resolved/unregistered/ambiguous，必须区分共享层与所有项目。纯读不登记。迁移 manifest 包含 schema/version/source hashes/source→target/ID→ID/scope sets/tags/link map/generation/journal。
- B：`parse_topic(path)` 返回 schema_version/id/name/description/memory_type/event_kind/status/unknown_metadata/body/provenance；`write_topic` 与 `patch_topic` 保留未知字段。合法 legacy 的 memory_type 可为 null；畸形文件返回诊断，不能假装成功。
- C：`enumerate_sources(context)` 去物理重复且排除管理文件；`search` 先 scope gate 再保持已有检索内核；导出稳定 memory ID 与 source path。`navigation_inventory(context, filters)` 返回 scoped distinct counts、最多 5 tags、已过滤的 route candidates，未知计数为 null，禁止把无 project 当作全库。registry/manifest 足以重建，不从旧 SQLite 推断权威身份。
- D：`render_brief`/`render_no_match` 接受 A 的 context 和 C 的 inventory，输出 2 KiB 内摘要/envelope；`render_context` 使用完整索引预算。results 与 navigation 字段分离；argparse 仅在 D 文件中接线。A/B/C 提供库函数，不能各改 CLI。
- E：先提交验收输入 schema、case IDs、八个开场 query、输出合同与 verifier 骨架，供 A–D 写测试；每期最终 integration 禁止依赖 mock 替代真实库行为。

可并行写各自模块/新测试；先固定 A/B 数据接口与 E contracts，再接 C，D 接线并与 E 完成端到端。依赖合并顺序不等于文件冲突。集成者也必须通过文件 owner 修改，不设置「全文件权限」第六条 lane。每期可以只合入该期用到的文件，后期命令不发布空实现。

## 10. 全局机器验收与不可回退基线

### 10.1 原 37 测试

现有测试文件保持冻结：`test_agent_memory.py` 11、`test_admin.py` 9、`test_snapshot.py` 5、`test_format.py` 8、`test_roadmap.py` 4，共 37。

```bash
python3 -B -m unittest discover -s eval/agent-memory/tests -p 'test_*.py'
```

实施前应得到 `Ran 37 tests` / `OK`；加测试后数量大于 37，但必须保留全部原 test ID 与断言，不靠删测试凑总数。E verifier 记录原测试文件 hash 和 test ID 集并检查原文件未变；如既有行为与新产品规则冲突，新增有版本边界的测试并保留 legacy API 兼容，不能悄悄改原断言。

### 10.2 冻结检索测量

在**未来实现开始前**，从当前已验收代码生成 baseline；生成后固定，不得用改后代码重新生成 before。本次计划任务没有执行测试或生成测量，下面是未来验收命令。

```bash
python3 -B eval/agent-memory/retrieval/measure.py \
  --corpus /home/agent/am/amcorpus \
  --output /tmp/agent-memory-arch-acceptance/before.json

# 实施候选版本上执行：
python3 -B eval/agent-memory/retrieval/measure.py \
  --corpus /home/agent/am/amcorpus \
  --output /tmp/agent-memory-arch-acceptance/after.json

python3 -B eval/agent-memory/retrieval/gate.py --phase 2 \
  /tmp/agent-memory-arch-acceptance/before.json \
  /tmp/agent-memory-arch-acceptance/after.json \
  > /tmp/agent-memory-arch-acceptance/gate.json

# 待 E lane 实现；现有 gate 之外的完整性校验：
python3 -B eval/agent-memory/verify_arch.py --check-retrieval \
  --before /tmp/agent-memory-arch-acceptance/before.json \
  --after /tmp/agent-memory-arch-acceptance/after.json \
  --gate /tmp/agent-memory-arch-acceptance/gate.json
```

每条命令均要求 exit 0；gate 必须 `status="PASS"`、`phase=2`、`failures=[]`、`budget.assessed=true`；verifier 输出 `status="PASS"`、`failures=[]`。before 另存代码 revision、文件 SHA256 与测量配置，运行结果只在 repo 外，不能提交 eval 日志。

只读核实的冻结输入：

```text
corpus count: 523
corpus_sha256: 150ec8cb4465da5200859c6590c74515e01e7fcaf8cc5217ef7f3718d580506c
judgments_sha256: f537a045d07cfe610937c8344005bb136d22736e87319d15f386c7f56ac58290
judgments count: 40
identifier: 6 queries
lexical-control: 12 queries
```

现有 gate 不独立验证输入 hash/完整性，E verifier 必须对 before/after 同时断言：

```python
assert report['input_valid'] is True
assert report['inputs']['corpus_sha256'] == FROZEN_CORPUS_SHA
assert report['inputs']['judgments_sha256'] == FROZEN_JUDGMENTS_SHA
assert report['baseline']['sync']['indexed'] == 523
assert report['baseline']['judgments'] == 40
assert len(report['baseline']['results']) == 40
assert len({r['id'] for r in report['baseline']['results']}) == 40
assert 'costs' in report
```

继承现有 gate 全部阈值，不降低：identifier 的 Recall@5/Recall@10/MRR@10 全为 1；lexical-control 两个 Recall 全为 1 且 MRR 不低于 baseline。**这两组每一题 rank 都必须不下降，null 直接失败**，不能用其它题提高均值抵消。其它题保留原 top-5/top-10 命中窗口；整体 Recall@5≥0.85、Recall@10≥0.925、MRR@10≥0.760 且不低于 baseline，CJK/mixed-language 继续原 phase 2 阈值。保留存储≤1.5×、median sync≤2×、overall/ASCII/Han p95≤`max(2×baseline, baseline+0.010s)` 的成本预算。

原 gate 基于原始 corpus-relative paths 和 v1 bindings，证明旧路径检索没退，不足以证明迁移成功。因此 C/E 另做迁移后同一 query 的稳定 memory ID 排序等价、scope 集等价、523 个原 source hash 保留、重建后等价；比较 manifest 中的路径映射，绝不修改原 judgments 来让迁移过关。

### 10.3 发布阻断条件

任何原测试失败、gate 非 PASS、identifier/lexical-control 任意一题退步、八个开场查询有死胡同、scope 泄漏、用户指令/自动加载文件被写入、原 source 丢失/被改写、migration rollback 不可恢复、预算超限或未执行必需 case，均停止该期发布。没有 runtime 的真实会话证据仅限制该 runtime 后续主动召回行为的验收声明，不能把缺测伪装成通过，也不能让 CLI 路径依赖专用宿主。

## 11. 存疑与需要的实验

不重写 brief 的事实。尚待验证的是我们扩展出来的边界：

1. Claude 是否原样保留 `metadata.agent_memory` 扩展尚无实测；本方案只做自有目录内便携包 round-trip，不写宿主自动加载文件，因此只承诺基础字段兼容，不承诺原生无损编辑。
2. 各 runtime 的 agent 在收到 no_match 导航后是否真的继续、压缩后是否重读：用第 9 节已安装 skill 的真实 session probes 验证。确定性 CLI 出口不等于模型行为已得到保证；不再研究用户已否决的宿主加载方案。
3. repo 移动、bare repo/submodule、多个 clone 的产品语义是我们的显式设计，超出 brief 的已实测集合；本地临时 git fixture 验证 resolver，不伪称 Claude 对这些边界也如此。
4. 50/150 行和字节预算是可测试的初始产品限制，没有证据证明它最优。只有实际 omitted 比例、任务漏召回和上下文成本测量表明不足时调整，并保留硬预算；不承诺相较既有紧凑输出再省固定百分比。

完成条件：三期各自达到对应机器验收；最终用户在 Claude Code、Codex、OpenCode 或裸终端均可主动访问同一全局库与同一 repo 记忆，无参数开场获得有界索引，零命中获得可执行导航，按需读文件/搜索，并能无损回滚旧库。所有实现均不得把索引或指令写进用户文件，不生成 runtime 自动加载文件。
