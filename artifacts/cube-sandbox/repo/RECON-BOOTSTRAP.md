# Agent Memory 安装、初始化与 UX 侦察

日期：2026-09-21

范围：只读审计 `skills/agent-memory/`、隔离环境实跑、外部 runtime 文档核对；未修改生产代码。

结论状态：**当前 bootstrap 在全新机器上不可闭环**。

## 0. 执行摘要

最重要的发现有四个：

1. `SKILL.md` 唯一的安装提示依赖 `${CLAUDE_PLUGIN_ROOT}`（`skills/agent-memory/SKILL.md:8-10`）。它在 Claude Code **plugin 内容加载**时是可替换占位符，但不是 Bash 主会话环境变量；在 Codex、裸终端和普通 skill copy 中不会成立。变量未定义时，原命令展开为 `/skills/agent-memory/scripts/setup.sh`，退出 127。
2. `setup.sh` 即使成功完成 `pnpm link --global`，在全新 HOME 里仍会退出 2：它在 `status` 失败后只运行 `init`，而 `init` 只写 settings、不建 SQLite index；最后一次 `status` 因 index 不存在而失败（`setup.sh:25-30`、`memory_config.py:82-86`、`memory_store_ext.py:43-45`）。
3. commit `6455a8fdfb71517c9df5c9f941d317bc0d652d82` 的 WAL/read-only 修复只部分有效：保留可读的非空 `-wal`/`-shm` 时 search 成功；冷 index 所在目录不可写、sidecar 不存在时，在本机 SQLite 返回 `attempt to write a readonly database`，代码只对 `unable to open database file` 做 immutable fallback，故仍退出 2（`memory_store_ext.py:63-81`）。
4. skill 文件安装和 CLI 安装是两件事。Claude marketplace / `npx skills` / chezmoi 可以让 runtime 找到 `SKILL.md` 和 sibling scripts，但只有当前的 root-package `pnpm link --global` 会生成裸命令；直接 Python 路径则无需 Node/pnpm。当前 `package.json` 的全局 link 还会同时暴露 `arcp`、`agent-memory`、`sandbox-ctl` 三个命令（`package.json:6-10`），不是 agent-memory 独占安装。

建议优先级：先修 `setup.sh` 的 `init + sync` 闭环和 SKILL 顶部 Preflight；再提供 skill-local Python fallback；随后补 read-only 回归测试及错误匹配；最后再评估 self-install、严格 path 和 doctor exit policy。

## 1. 范围、方法与隔离

- 所有首次运行、损坏库、只读库实验都使用 `/tmp/am-*.XXXXXX` 的临时 HOME；没有读写 `/home/agent/.agent-memory`，没有改 Codex/Paseo 配置。
- CLI 使用仓库中的真实 `skills/agent-memory/scripts/agent_memory.py`；安装实验使用真实 `setup.sh`、pnpm 和 `npx skills`。
- 基础环境：Python `3.12.12`、SQLite `3.40.1`（FTS5 可创建）、Node `v22.22.0`、npm/npx `10.9.4`。系统 `pnpm` shim 存在但 Corepack payload 损坏；另在临时 HOME 安装 pnpm `10.15.1` 以继续验证 setup 的后半段。
- 现有回归测试实跑：`python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py'`，**37 tests，OK**。commit `6455a8f` 未加入测试；现有 suite 没有 chmod/WAL read-only 或 setup bootstrap 覆盖。
- `AGENTS.md` 与 `CLAUDE.md` 只规定仓库布局、测试/评测位置和开发纪律，没有 agent-memory 安装说明。当前安装说明只散落在 `README.md:33-50`、`SKILL.md:8-10`、`docs/agent-memory.md:338-390` 和脚本本身。
- `README.md:54-57` 的 `pnpm install` 位于 Development 段，只安装仓库开发依赖，并不把 CLI link 到全局；`docs/agent-memory.md:358-367` 的 “bootstrap” 从 `agent-memory init` 开始，已经假定裸命令存在。这两处都不能补上 command-not-found 缺口。

## 2. 真实安装形态拆解

### 2.1 root package + `pnpm link --global`

实际机制：

1. `setup.sh` 从自身路径计算 `script_dir` 和 `repo_root`（`setup.sh:9-10`）。
2. 只用 `command -v pnpm` 检测 pnpm（`:12-15`），不能发现“shim 存在但 Corepack payload 缺失”。
3. 运行 `pnpm --dir "$repo_root" link --global`（`:17-18`）。
4. root `package.json:6-10` 把三个 bin 映射到仓库脚本；`agent-memory` 指向可执行的 `skills/agent-memory/scripts/agent_memory.py`。没有 build/copy 步骤。
5. **从仓库根调用时**，pnpm 在 `$PNPM_HOME` 生成三个 command shim，并在 pnpm global store 中建立 `my-claude-plugins -> <checkout>` link；因此命令依赖原 checkout 继续存在。仓库外 CWD 的反例见下文。

临时 HOME 中的实际产物：

```text
$PNPM_HOME/agent-memory
$PNPM_HOME/arcp
$PNPM_HOME/sandbox-ctl
$PNPM_HOME/global/5/node_modules/my-claude-plugins -> /home/agent/am/r2
~/.agent-memory/settings.json
```

前置条件：可工作的 Node + pnpm、`$PNPM_HOME` 已在 PATH、运行期 `python3`、Python 自带 SQLite 且启用 FTS5。源码使用 `Path | None` 等 union 语法（如 `memory_config.py:54`），实际最低边界是 Python 3.10，而不是文档笼统写的“Python 3”（`docs/agent-memory.md:338-340`）。

额外实测风险：用 pnpm 10.15.1 从 `/tmp` 调用绝对路径 setup，虽然脚本传了 `--dir "$repo_root"`，global link 实际链接了调用 CWD `/tmp`：

```text
$ cd /tmp && /home/agent/am/r2/skills/agent-memory/scripts/setup.sh
agent-memory was linked but is not on PATH; add pnpm's global bin directory to PATH
exit=1
$ find "$PNPM_HOME/global/5/node_modules" -type l
.../node_modules/tmp -> ../../../../..
```

从仓库根运行时才链接到本仓库。这个 pnpm 版本下，`--dir` 没有让 `link --global` 使用期望 package；setup 没有显式 `cd "$repo_root"`。因此当前安装结果还意外依赖调用 CWD。

### 2.2 直接运行 Python（skill-local、零第三方 Python 依赖）

可用入口：

```sh
python3 /absolute/path/to/agent-memory/scripts/agent_memory.py --json init
python3 /absolute/path/to/agent-memory/scripts/agent_memory.py --json sync
python3 /absolute/path/to/agent-memory/scripts/agent_memory.py --json search "x"
```

`agent_memory.py:1-32` 只有 stdlib 和 sibling `memory_*` imports；全 scripts AST/import 扫描未发现第三方包。注意它是“**skill 目录自包含**”，不是物理单文件：只复制 `agent_memory.py` 会因 sibling imports 失败。运行前置为 Python >=3.10 + sqlite3/FTS5；不需要 Node/pnpm。

### 2.3 `npx skills`

当前 tracked tree 和全部 git 历史中都没有 `npx skills` 安装说明；这是外部通用 skill installer，不是仓库已承诺的安装流程。真实执行：

```text
$ npx --yes skills add . --list
codex  Agent detected — installing non-interactively
Source: /home/agent/am/r2
Local path validated
Found 11 skills
... agent-memory ...
exit=0
```

再用隔离 HOME 实装：

```text
$ npx --yes skills add . --skill agent-memory -g -a codex -y --copy
Selected 1 skill: agent-memory
~/.agents/skills/agent-memory
  copy → Codex
✓ agent-memory (copied)
exit=0
```

产物只有 `SKILL.md`、`references/*`、`scripts/*`，没有 root `package.json`，也没有生成 `agent-memory` PATH command。它满足 Codex 的 skill discovery，但仍需直接 Python fallback 或另一个 CLI installer。安装阶段需要 Node/npm/npx；运行 Python入口不需要它们。`npx skills` 官方 README 说明支持本地路径并会发现 `skills/`（[Vercel skills README](https://github.com/vercel-labs/skills/blob/main/README.md)）。

复制后的 `scripts/setup.sh` 从自身向上三级算出的所谓 `repo_root` 是 `~/.agents`，不是含 `package.json` 的原仓库；而本机 pnpm 10.15.1 的 `link --global` 又实测使用调用 CWD。两种结果都说明 standalone skill copy 不能把当前 setup 当成可靠 installer。

### 2.4 Claude Code plugin marketplace

仓库 manifest 把 `./skills/agent-memory` 纳入 `my-skills` plugin（`.claude-plugin/marketplace.json:7-16`）；README 只说配置 marketplace 并启用 plugin（`README.md:48-50`）。这会安装/加载 skill 文件，**不会因为 root `package.json.bin` 自动安装 CLI**。

按 Claude 官方文档的 **Standard plugin layout** 段，plugin root 的 `bin/` executable 会加入 Bash tool PATH，但本仓库没有这个目录；root npm `bin` metadata 不是 Claude plugin `bin/`（[Claude plugin layout](https://code.claude.com/docs/en/plugins-reference)）。文档的 **Plugin caching and file resolution** 段说 marketplace 通常复制到 `~/.claude/plugins/cache`，本地目录 marketplace 可原地加载；**Environment variables** 段说 `${CLAUDE_PLUGIN_ROOT}` 会在 skill/agent content 中 inline substitution，同时不会作为普通 Bash tool/main session/subagent 的环境变量存在（[Claude plugin path variables](https://code.claude.com/docs/en/plugins-reference)）。这些 Claude 行为均为文档核对，本沙箱未端到端验证。

因此：Claude plugin 这一形态有机会把 `SKILL.md:10` 替换为正确绝对路径，但 setup 仍受 pnpm/CWD/init 缺陷影响；Codex 或裸读 SKILL.md 则只会看到未定义变量。

本 lane 没有真实安装 Claude marketplace，cache 路径和 inline substitution 依据官方文档与本仓库 manifest，标记为**未在本沙箱端到端验证**。

## 3. 失败矩阵（真实执行）

以下命令中的 `repo` 与 `cli` 是复现前缀；所有 destructive 操作只指向刚创建的 `/tmp/am-*`：

```sh
repo=/home/agent/am/r2
cli="$repo/skills/agent-memory/scripts/agent_memory.py"
tmp_home=$(mktemp -d /tmp/am-repro.XXXXXX)
```

### 3.1 `${CLAUDE_PLUGIN_ROOT}` 未定义

复现：

```sh
env -u CLAUDE_PLUGIN_ROOT bash -c '"${CLAUDE_PLUGIN_ROOT}/skills/agent-memory/scripts/setup.sh"'
```

实际：

```text
bash: line 1: /skills/agent-memory/scripts/setup.sh: No such file or directory
exit=127
```

裸 SKILL 消费者无法从此错误推出真实 skill 目录。Claude plugin inline substitution 是唯一例外，不应被写成跨 runtime 假设。

### 3.2 command 未安装

复现：

```sh
PATH=/usr/bin:/bin agent-memory --json search "x"
```

实际：

```text
/bin/bash: agent-memory: command not found
exit=127
```

错误没有安装位置、Python fallback、需要的依赖或下一条命令；只拿到 SKILL.md 的 agent 不能可靠自救。

### 3.3 全新 HOME 运行 setup

先按沙箱原状运行。`command -v pnpm` 成功，但执行 pnpm 失败：

```text
! Corepack is about to download https://registry.npmjs.org/pnpm/-/pnpm-12.5.1.tgz
Error: Cannot find module '/tmp/am-setup-home.../.cache/node/corepack/v1/pnpm/12.5.1/bin/pnpm.cjs'
Node.js v22.22.0
exit=1
```

随后在临时 HOME 安装 pnpm 10.15.1，并把临时 `$PNPM_HOME` 放进 PATH，从仓库根再次运行真实 setup：

```sh
tmp_home=$(mktemp -d /tmp/am-setup-success.XXXXXX)
HOME="$tmp_home" npm install --global --prefix "$tmp_home/pnpm-prefix" pnpm@10.15.1
HOME="$tmp_home" PNPM_HOME="$tmp_home/pnpm-home" \
  PATH="$tmp_home/pnpm-prefix/bin:$tmp_home/pnpm-home:/usr/local/bin:/usr/bin:/bin" \
  /home/agent/am/r2/skills/agent-memory/scripts/setup.sh
```

```text
$ HOME=/tmp/am-setup-success... PNPM_HOME=... setup.sh
agent-memory: index not initialized, run: agent-memory sync
exit=2
$ command -v agent-memory
/tmp/am-setup-success.../pnpm-home/agent-memory
```

命令**已在 PATH**，三个 pnpm shims 和 global repo link 已产生，settings 也已写入；但没有 `index.sqlite3`。根因是 `status || init; status`，而不是 `init; sync; status`。

若 settings 已存在但 DB 缺失，setup 更差地掩盖根因：

```text
$ setup.sh
agent-memory: settings already exist: /tmp/am-setup-existing.../.agent-memory/settings.json
exit=2
```

即第一次 `status` 因 index 缺失失败，setup 无条件调用 `init`，最后显示“settings already exist”，而不是建议 sync。

### 3.4 只有 Python、没有 Node/pnpm

使用只含 `python3` symlink 的临时 PATH，并用精确脚本路径调用；`command -v node/pnpm` 均失败。实际序列：

```sh
tmp_home=$(mktemp -d /tmp/am-python-only.XXXXXX)
tmp_bin=$(mktemp -d /tmp/am-python-bin.XXXXXX)
ln -s /usr/local/bin/python3 "$tmp_bin/python3"
env -i HOME="$tmp_home" PATH="$tmp_bin" python3 \
  /home/agent/am/r2/skills/agent-memory/scripts/agent_memory.py --json status
# 后续把 status 依次替换为 init、status、doctor、search x、sync、search x
```

```text
$ python3 .../agent_memory.py --json status
agent-memory: settings not found: /tmp/am-python-only.../.agent-memory/settings.json; run `agent-memory init` first
exit=2

$ python3 .../agent_memory.py --json init
{"settings":"/tmp/am-python-only.../.agent-memory/settings.json"}
exit=0

$ python3 .../agent_memory.py --json status
agent-memory: index not initialized, run: agent-memory sync
exit=2

$ python3 .../agent_memory.py --json search x
agent-memory: index not initialized, run: agent-memory sync
exit=2
```

再执行 `sync` 后，search 输出 `[]`、exit 0。结论：纯 Python路径自洽、没有第三方 Python dependency，但完整首次 bootstrap 仍是 `init` **加** `sync`，且需要整个 `scripts/` sibling module 集合。

### 3.5 首次 settings/data 初始化

`init` 创建的精确默认配置是：

```json
{
  "version": 1,
  "database": "index.sqlite3",
  "shared": [],
  "bindings": []
}
```

行为矩阵：

| 状态/命令 | 实际结果 | exit |
|---|---|---:|
| settings 不存在：`status` | `settings not found ... run agent-memory init first` | 2 |
| settings 不存在：`doctor` | structured `settings_invalid` | 2 |
| `init` | 只创建 settings | 0 |
| init 后 `status` | `index not initialized, run: agent-memory sync` | 2 |
| init 后 `doctor` | structured `database_missing` | 2 |
| `sync` | `indexed=0, removed=0, roots=0`，创建 DB | 0 |
| sync 后 `status` | documents/links/stable_ids 均为 0 | 0 |
| sync 后 `doctor` | `status: ok` | 0 |

“健康空 registry”仍没有 project binding/capture root，因此 `capture` 不可用。`init` 的名字和 setup 的处理方式都容易让 agent 误以为初始化已完成。

settings 路径还有两个只读源码才会发现的 override：`AGENT_MEMORY_SETTINGS`，其次 `AGENT_MEMORY_HOME`，最后才是 `~/.agent-memory/settings.json`（`memory_config.py:62-68`）；SKILL.md 没写。

### 3.6 index 不存在、损坏、只读

**不存在**：如上，status/search 纯文本报 `index not initialized, run: agent-memory sync`，exit 2；doctor 返回 `database_missing`，exit 2，且不会偷偷创建 DB（对应现有测试 `eval/agent-memory/tests/test_admin.py:137-149`）。

**损坏**：在临时 HOME 把 4096 bytes 随机数据作为 index：

```sh
tmp_home=$(mktemp -d /tmp/am-corrupt.XXXXXX)
HOME="$tmp_home" python3 "$cli" --json init
dd if=/dev/urandom of="$tmp_home/.agent-memory/index.sqlite3" bs=4096 count=1 status=none
HOME="$tmp_home" python3 "$cli" --json status
HOME="$tmp_home" python3 "$cli" --json search x
HOME="$tmp_home" python3 "$cli" --json doctor
```

```text
$ agent-memory --json status
agent-memory: file is not a database
exit=2
$ agent-memory --json search x
agent-memory: file is not a database
exit=2
$ agent-memory --json doctor
{"status":"error",...,"checks":[{"code":"doctor_failed","message":"file is not a database",...}]}
exit=2
```

错误指出 DB path，但没有安全恢复提示。`sync` 不能直接修复现有 corrupt file；人工恢复应先解析并确认精确 DB path、停止 writers，把 DB 与同名 `-wal`/`-shm` **作为一组可恢复地移到 quarantine/backup**，再运行 sync，绝不能自动删除。

**commit `6455a8f` 的成功路径**：初始化 DB 后，保持 writer connection 打开、制造 12392-byte 非空 WAL 与 32768-byte SHM，再把 settings/DB/sidecars chmod 0444、目录 chmod 0555：

```sh
tmp_home=$(mktemp -d /tmp/am-readonly-wal.XXXXXX)
HOME="$tmp_home" python3 "$cli" --json init
HOME="$tmp_home" python3 "$cli" --json sync
python3 - "$tmp_home" "$cli" <<'PY'
import os, pathlib, sqlite3, subprocess, sys
home=pathlib.Path(sys.argv[1]); cli=sys.argv[2]; d=home/'.agent-memory'; db=d/'index.sqlite3'
c=sqlite3.connect(db); c.execute('PRAGMA journal_mode=WAL')
c.execute('CREATE TABLE wal_probe(value TEXT)')
c.execute("INSERT INTO wal_probe VALUES ('visible-only-through-wal')"); c.commit()
for p in d.iterdir(): os.chmod(p, 0o444)
os.chmod(d, 0o555); os.chmod(home, 0o555)
r=subprocess.run(['python3', cli, '--json', 'search', 'x'],
                 env={**os.environ, 'HOME': str(home)}, text=True, capture_output=True)
print(r.stdout, r.stderr, 'exit='+str(r.returncode), sep='')
c.close()
PY
```

```text
sidecars before: [('index.sqlite3-shm', 32768), ('index.sqlite3-wal', 12392)]
$ agent-memory --json search "x"
[]
exit=0
```

这证明 read-only connection 能使用已有 WAL/SHM，修复的主要路由（`agent_memory.py:36-55,402-405`）确实生效。

**commit 的失败路径**：冷 DB 无 sidecars，settings/DB 0444、目录 0555：

```sh
tmp_home=$(mktemp -d /tmp/am-readonly-cold.XXXXXX)
HOME="$tmp_home" python3 "$cli" --json init
HOME="$tmp_home" python3 "$cli" --json sync
db="$tmp_home/.agent-memory/index.sqlite3"
unlink "$db-shm" 2>/dev/null || true
unlink "$db-wal" 2>/dev/null || true
chmod 0444 "$tmp_home/.agent-memory/settings.json" "$db"
chmod 0555 "$tmp_home" "$tmp_home/.agent-memory"
HOME="$tmp_home" python3 "$cli" --json search x
HOME="$tmp_home" python3 "$cli" --json status
```

```text
$ agent-memory --json search "x"
agent-memory: attempt to write a readonly database
exit=2
$ agent-memory --json status
agent-memory: attempt to write a readonly database
exit=2
```

手工以 `mode=ro&immutable=1` 打开同一 DB 成功。失败点是普通 `mode=ro` 在 `PRAGMA schema_version` 触发了不同的 SQLite 错误文本；`memory_store_ext.py:65-67` 只允许包含 `unable to open database file` 的异常进入 immutable fallback。当前 `references/doctor-sync-settings.md:51-56` 仍声称所有 read 都以写连接跑 DDL，是 `6455a8f` 之前的陈旧 caveat，应更新。

## 4. Agent 视角 UX 审计

### P0：还没拿到命令就进入 dead end

- `SKILL.md:8-10` 没有 `command -v`、fallback、最低 Python 版本、FTS5 检查或 init/sync 顺序。
- 第一条高频命令已经是裸 `agent-memory`（`:12-16`）；command-not-found 不提供可推导路径。
- 按 Claude 官方 **Generate visual output** 示例，`${CLAUDE_SKILL_DIR}` 可在 personal/project/plugin skill 层级正确解析（[Claude skill path guidance](https://code.claude.com/docs/en/skills)）；本沙箱未验证。`${CLAUDE_PLUGIN_ROOT}` 对那条原始命令的 Claude plugin content substitution 是特例，对 Codex/plain skill/bare terminal 无效。跨 runtime 文案仍需明确 Python 相对入口，不能假定 Claude placeholder。
- setup 成功条件没有闭环；agent 即使猜到 setup 也会得到 exit 2。

### P0：read path 的“软回退”会扩大作用域

`SKILL.md:25-27` 确实写了 search 错 path 会 global fallback；`:40-43` 也写了 capture 更严格，所以“不一致”并非完全未文档化。但产品风险仍高：拼错一个 project path 会从 scoped recall 悄悄扩大为全局 recall。

真实构造一条 `demo` memory 后：

```text
$ agent-memory --json search "Personal Claude Code plugin marketplace" --path /tmp/.../definitely-unbound
[{"title":"my-claude-plugins",...,"projects":["demo"]}]
exit=0
$ agent-memory --json capture learning "Should fail" --path /tmp/.../definitely-unbound
agent-memory: no project binding matches path: ...; run `agent-memory projects` or `agent-memory browse`, then pass --project <name>
exit=2
```

源码 `_query_project` 吞掉 `UnboundPathError`/`AmbiguousDescendantBindingError` 返回 global（`agent_memory.py:264-275`）；capture 严格 resolve，并且没传 target 时默认当前 CWD（`:420-438`）。另外 `--project typo` 在 read path 不校验、只返回空结果（`:268-269`），capture 则验证 project（`memory_config.py:308-321`）。

### P1：doctor 的非零码同时承担“诊断严重度”和“能否继续”

CLI 明确映射 `ok=0 / warn=1 / error=2`（`agent_memory.py:316-341`；`references/doctor-sync-settings.md:21-27`）。根因不是 doctor 不能 search，而是 shell `&&` 会因 warning=1 short-circuit。真实制造一个未索引 Markdown 后，doctor 返回 `status: warn`、exit 1；`doctor && search` 的 RHS marker 没有出现。

`SKILL.md:28-29` 已警告不要链，但称 warning “harmless”不够准确：missing root、stale/unindexed Markdown、dangling links 都可能需要行动。更好的产品接口应把“health severity”与“shell gating policy”拆开。

### P1：只有读源码才知道或文档不足的行为

- `init --force` 会无备份覆盖 settings（`memory_config.py:82-86`），不应被自动 recovery 使用。
- 空 init + sync 会成为 doctor 眼中的健康 registry，但 capture 无目标可写。
- `resolve` 是 init 之外唯一可以不打开 DB 的普通命令（`agent_memory.py:377-401`）；`projects` 即使只列配置也要求已初始化 DB。
- 相对 project memory root 以 binding path 为基准；相对 shared root 以 settings 目录为基准；顶层 binding 必须绝对，nested project 可以相对（`memory_config.py:141-233`）。
- capture 的 `--root` 必须精确匹配 configured resolved root（`memory_capture.py:27-44`）。
- read-only fallback 若用 immutable 会向 stderr 警告；若 WAL 非空且无法安全读取，会要求在可写环境 sync（`memory_store_ext.py:62-81`）。
- doctor 在 DB 不存在时提前返回，不能完成首次机器的 routing/root 检查；`doctor --path` 的 resolve exception 又被压成泛化 `doctor_failed`，paths 指向 DB 而非错误 worktree（`agent_memory.py:324-340`）。
- package-wide global link 会顺带安装另外两个 CLI，存在命令碰撞和超出用户预期的副作用。

## 5. 候选改进方向（只提案，不实现）

| 优先级 | 机制 | 解决的失败 | 成本 | 风险/兼容性 |
|---|---|---|---|---|
| P0 | 在 SKILL 开头增加 3 行 Preflight：检查 `command -v agent-memory`；缺失时显示 skill-local Python 入口；settings/DB 不存在时明确 `init` + `sync` | command-not-found、环境变量猜测、首次 DB 缺失 | 低 | 不同 runtime 如何表达 skill dir；需提供 Claude 与 generic 两种写法 |
| P0 | 修 setup：显式 `cd "$repo_root"` 后 link；区分 settings 缺失和 index 缺失；只在 settings 缺失时 init，index 缺失时 sync，最后 status | CWD 错 link、fresh setup exit 2、existing settings 被误 init | 低 | `sync` 会创建 DB；应在临时 HOME 做幂等测试 |
| P0 | 把 `python3 <skill>/scripts/agent_memory.py` 作为第一等零第三方 fallback；写清 Python >=3.10 + sqlite/FTS5；保留整个 scripts 目录 | 无 Node/pnpm、npx/Codex copy 无裸命令 | 低 | “单文件”措辞会误导；实际依赖 sibling modules |
| P0 | 修 read-only fallback：按 SQLite error code/readonly 类别而不是单一英文字符串决策；**只有确认 WAL 不存在/为空并完成 race recheck 后**才允许 immutable；覆盖 cold/no-sidecar、existing WAL+SHM、nonempty WAL unsafe 三类测试 | chmod read-only 冷库仍失败 | 中 | immutable 不能看未 checkpoint WAL；必须保持当前拒绝条件和 race recheck |
| P1 | 新增 `self-install`/`bootstrap` 子命令：在用户 bin 创建稳定 shim，先做 requirements 检查，再 init/sync；打印 PATH 行动项 | 裸 CLI discoverability、pnpm SPOF | 中 | plugin cache 升级/卸载会让 symlink stale；复制全 scripts 又有版本漂移。应使用稳定 data dir 或可重装 shim |
| P1 | `${CLAUDE_PLUGIN_ROOT}` 健壮回退：定位 setup 后由 setup 的 `$0`/`__file__` 推导 skill root；Claude 文案优先 `${CLAUDE_SKILL_DIR}`；generic runtime 使用被加载 skill 的实际 path | Claude-only 假设、skill copy | 低-中 | 没有一个 placeholder 跨所有 runtime；不可凭 CWD 猜 skill path |
| P1 | Claude marketplace 增加 plugin-root `bin/agent-memory` wrapper，relative/self-path exec Python | Claude plugin 安装后立即有裸命令 | 低 | 只帮助完整 plugin，不帮助 standalone skill copy；bundle 中全局暴露行为需明确 |
| P1 | doctor 增 `--exit-code=errors` / `--advisory`，或独立 `ready` 命令；保留默认 0/1/2 | `doctor && search` 误跳过 | 低-中 | 改默认码会破坏 CI，故应新增 opt-in policy |
| P1 | 首次运行自动 init 仅放在显式 mutating/bootstrap path；或 `init --sync` / `bootstrap`。不要让普通 search 隐式写盘 | init 后还缺 DB、空机器 onboarding | 中 | 对 read-only search 自动写会违反预期，并可能把配置错误伪装成“空结果” |
| P1 | guided init：可选 `--project-path`、`--memory-root`、`--sync`，并在空配置 status 中显示 `capture unavailable` | “健康但不可 capture”的空 registry | 中 | 交互式流程不适合 unattended；必须保留非交互 flags |
| P2 | search 的错 path 改为 strict，另设显式 `--global` 或过渡期 `--fallback-global`；read `--project` 也校验 | typo 导致跨项目全局召回/空假阴性 | 中 | 行为兼容风险最高；需迁移 warning 和 release note |
| P2 | corrupt DB 错误加入精确提示：确认 DB path、停止 writers、将 DB/WAL/SHM 整组 quarantine 后再 `sync` rebuild；更新 stale read-only reference | agent 不知道恢复动作 | 低 | 绝不自动删除；错误 path 或遗漏 sidecar 会造成数据损失 |

推荐的最小 Preflight 语义（不是最终跨 runtime 语法）：

```sh
command -v agent-memory >/dev/null 2>&1 || use-skill-local-python-entrypoint
agent-memory --json status >/dev/null 2>&1 || agent-memory --json init
agent-memory --json status >/dev/null 2>&1 || agent-memory --json sync
```

实现时不能原样用第二行的宽泛 `|| init`；应先判断 settings 是否存在，否则会重复当前“已有 settings 时误 init”的 bug。更好的做法是一个内部能分类状态的幂等 `bootstrap`。

## 6. Runtime / 文件布局兼容性边界

### Claude Code plugin

- 需要 plugin root 下 `skills/<name>/SKILL.md` 或 manifest 显式 skills path；当前 `.claude-plugin/marketplace.json:12-14` 满足。
- Marketplace copy 可能位于版本化 cache；local-directory marketplace 可原地加载。把全局 shim永久指向 cache version 有 stale 风险。
- 按 Claude 官方 **Environment variables** 段，plugin content 会 inline substitute `${CLAUDE_PLUGIN_ROOT}`，普通 Bash 环境没有这个变量；按其 skill 示例，`${CLAUDE_SKILL_DIR}` 是 personal/project/plugin skill 都适用的 skill-local 机制。本沙箱均未端到端验证。
- 按 Claude 官方 **Standard plugin layout** 段，plugin root `bin/` executable 会加入 Bash tool PATH；当前仓库未使用这一原生能力，本沙箱未验证。

### Codex skill

- 官方格式是 skill directory + `SKILL.md`，可带 `scripts/`、`references/`、`assets/`；当前 agent-memory 原子目录符合（[OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills)）。
- Codex 从 CWD 到 repo root 扫 `.agents/skills`，并读取 `$HOME/.agents/skills`、`/etc/codex/skills`。它不读取 Claude marketplace manifest，也不提供 `${CLAUDE_PLUGIN_ROOT}`。
- `npx skills` 实测把此 skill copy 到 `~/.agents/skills/agent-memory`；只保证 skill 被发现，不保证 CLI 在 PATH。

### 裸 CLI / CI / 其它 agent runtime

- 最稳的共同分母是保留 `agent-memory/{SKILL.md,scripts/,references/}` 完整目录，用已知绝对 skill path直接运行 Python。
- 如果 runtime 遵循 Agent Skills 目录约定，它通常能发现 `SKILL.md`，但 placeholder 和 PATH injection 各不相同；不能把“skill loaded”等同于“bin installed”。
- root-package pnpm link 更适合稳定源码 checkout/dev machine，不适合只复制单 skill 的 installer，也不适合临时 plugin cache lifecycle。

### `npx skills` 与 chezmoi 线索

- 当前仓库没有 `npx skills` 文档，但外部 installer 能发现当前 `skills/` 布局，且实测安装到 Codex 官方 `$HOME/.agents/skills` 路径。
- 当前 tracked tree 没有 chezmoi 安装实现。历史删除的 transient audit（commit `bfbf47d` 加入、`c9b5b9e` 删除）只说明 owner 曾把 personal skill 存在 `/Users/fanye/.local/share/chezmoi/...`，运行态暴露到 `~/.agents/skills/...`；这不是 agent-memory 的受支持 installer。
- chezmoi 官方模型是从 `~/.local/share/chezmoi` source dir copy/template 到 home（[chezmoi setup](https://www.chezmoi.io/user-guide/setup/)）。若采用它，仍只是部署 skill 文件；裸命令需要另行处理。

## 7. 建议的验收测试清单

后续实现至少应把以下测试放在 `eval/agent-memory/tests/`，不放进 production skill：

1. temp HOME、无 settings：setup/bootstrap 后 settings + DB 均存在，status=0，重复执行仍为 0。
2. settings 已存在、DB 缺失：不调用 destructive `init --force`，只 sync，最终 status=0。
3. 从 repo 外 CWD 调 setup，global link 仍精确指向预期 repo；只安装声明范围内命令或明确 package-wide 副作用。
4. PATH 没有 Node/pnpm：skill-local Python `init; sync; search` 成功。
5. read-only cold DB 无 sidecars：immutable fallback 成功且警告；非空 WAL 无 SHM：安全拒绝；已有可读 WAL+SHM：查询成功且能看到 WAL。
6. 损坏 DB：错误带明确、非 destructive rebuild 指引。
7. wrong/unbound search path 与 capture path 的预期契约；若保留 soft fallback，结果必须显式标注 `scope_fallback: global`。
8. doctor warning exit 1 与 opt-in gating policy；确保 `search` readiness 不被普通 warning 阻断。
9. `npx skills --copy` 后，不依赖 root package.json，直接 Python fallback 可运行。

## 8. 最终判断

agent-memory 的运行核心其实很适合 portable bootstrap：Python stdlib + SQLite/FTS5、文件优先、无需 daemon。但当前安装层把这个优势隐藏在 Node/pnpm global link、Claude-specific placeholder 和不完整 init 流程后面。最小且高收益的修复不是先造复杂 installer，而是：

1. SKILL 顶部明确 Preflight 与 skill-local Python fallback；
2. 把 setup/bootstrap 做成真正幂等的 `locate -> install command -> init settings -> sync index -> status`；
3. 为 cold read-only 和首次 setup 加回归测试；
4. 明确区分“skill 文件已安装”和“CLI 命令已安装”。

做到这四点后，Claude marketplace、Codex/npx skill copy、裸终端和 CI 才会共享一个可推导、可恢复的 onboarding contract。
