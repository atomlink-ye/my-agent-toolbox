# Lane C — bootstrap 脚本

## 做了什么

- `setup.sh` 从自身 `BASH_SOURCE` 定位完整 `scripts/` 目录，不依赖调用 CWD 或 `CLAUDE_PLUGIN_ROOT`。
- Python 入口成为第一等 bootstrap 路径；先校验 Python >= 3.10、stdlib `sqlite3` 与 FTS5。
- 通过现有 `memory_config.default_settings_path()`、`load_settings()`、`database_path()` 解析 settings/DB，未复制另一套路径优先级。
- 只在 settings 文件确实不存在时执行 `init`；只在 DB 文件确实不存在时执行 `sync`；已有 DB 只执行只读 `status`，不隐式 sync。
- malformed settings、损坏 DB 和其他 I/O 错误分类失败，不运行 `init --force`，不移动或删除用户文件。
- 只有 root manifest、包名、三个 bin 表面及 `agent-memory` 映射全部正确，且 Node/pnpm 可实际运行时，才在显式 `cd "$repo_root"` 的子 shell 中执行 package-wide global link。
- standalone copy 或不可用 pnpm 会明确报告 Python 入口就绪、裸命令未安装，并提醒保留完整 scripts 目录。

## 红先行证据（修改前）

工作 pnpm 10.15.1 下 fresh HOME：

```sh
HOME="$red_home" PNPM_HOME="$red_home/pnpm-home" \
  PATH="$pnpm_bin:$red_home/pnpm-home:/usr/local/bin:/usr/bin:/bin" \
  skills/agent-memory/scripts/setup.sh
```

```text
exit=2
agent-memory: index not initialized, run: agent-memory sync
```

从 `/tmp` 调用同一脚本：

```sh
(cd /tmp && HOME="$red_tmp_home" PNPM_HOME="$red_tmp_home/pnpm-home" \
  PATH="$pnpm_bin:$red_tmp_home/pnpm-home:/usr/local/bin:/usr/bin:/bin" \
  /home/agent/am/c/skills/agent-memory/scripts/setup.sh)
find "$red_tmp_home/pnpm-home/global" -type l -printf '%p -> %l\n'
```

```text
exit=1
agent-memory was linked but is not on PATH; add pnpm's global bin directory to PATH
.../global/5/node_modules/tmp -> ../../../../..
```

无可用 pnpm 的原脚本也会阻断：

```sh
HOME="$tmp_home" skills/agent-memory/scripts/setup.sh
```

```text
exit=1
Error: Cannot find module '.../pnpm/12.5.1/bin/pnpm.cjs'
```

## 修改后真实验收

以下 standalone 验收先复制了完整 skill 目录，并在 PATH 中只提供 `python3`；脚本由 `/bin/bash` 以绝对路径调用，因此没有 Node/pnpm，也没有依赖调用 CWD。

### Fresh HOME 与重复执行

```sh
env -i HOME="$run_home" PATH="$py_bin" /bin/bash \
  "$copy_root/agent-memory/scripts/setup.sh"
sha256sum "$run_home/.agent-memory/settings.json" \
  "$run_home/.agent-memory/index.sqlite3"
# 原命令重复一次并再次计算两个 hash
```

```text
fresh exit=0
documents=0, links=0, resolved_links=0, stable_ids=0
agent-memory setup: Python entry ready: .../python3 .../scripts/agent_memory.py
agent-memory setup: bare command not installed; keep the complete scripts directory
repeat exit=0
settings before/after:
c827c80258a77e36c402369c76f9df1f53da5a8746fc326cc0cdb7366a82be68
c827c80258a77e36c402369c76f9df1f53da5a8746fc326cc0cdb7366a82be68
DB before/after:
15312b4a3162400b4c9713a583fd3018ec092d1835f2f72d9d2f7ff4e2739d4a
15312b4a3162400b4c9713a583fd3018ec092d1835f2f72d9d2f7ff4e2739d4a
```

相同 DB hash 也证明健康 index 的重复 setup 没有擅自 sync。

### settings 已有、index 缺失

```sh
env -i HOME="$missing_home" PATH="$py_bin" "$py_bin/python3" \
  "$copy_root/agent-memory/scripts/agent_memory.py" --json init
env -i HOME="$missing_home" PATH="$py_bin" /bin/bash \
  "$copy_root/agent-memory/scripts/setup.sh"
```

```text
exit=0
documents=0, links=0, resolved_links=0, stable_ids=0
settings before/after:
c827c80258a77e36c402369c76f9df1f53da5a8746fc326cc0cdb7366a82be68
c827c80258a77e36c402369c76f9df1f53da5a8746fc326cc0cdb7366a82be68
```

### malformed settings

```sh
echo '{bad' > "$mal_home/.agent-memory/settings.json"
env -i HOME="$mal_home" PATH="$py_bin" /bin/bash \
  "$copy_root/agent-memory/scripts/setup.sh"
```

```text
exit=2
agent-memory setup: invalid settings at .../settings.json: invalid JSON in .../settings.json: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
settings before/after:
0fad202fbcabc5aa98fc722f02ebcd19326a9fdfb010a9962aca72ab295d0c37
0fad202fbcabc5aa98fc722f02ebcd19326a9fdfb010a9962aca72ab295d0c37
```

### 损坏 DB

```sh
HOME="$bad_home" python3 .../agent_memory.py --json init
head -c 4096 /dev/urandom > "$bad_home/.agent-memory/index.sqlite3"
env -i HOME="$bad_home" PATH="$py_bin" /bin/bash \
  "$copy_root/agent-memory/scripts/setup.sh"
```

```text
exit=2
agent-memory: file is not a database
agent-memory setup: existing index is unhealthy; settings and database were left unchanged
DB before/after:
928b90f0401f4c05a9c27a19610e935c56c705d5c626dddc71c2f8f642e81b8a
928b90f0401f4c05a9c27a19610e935c56c705d5c626dddc71c2f8f642e81b8a
```

### 从 `/tmp` 执行真实 pnpm 10.15.1 link

```sh
(cd /tmp && HOME="$link_home" PNPM_HOME="$link_home/pnpm-home" \
  PATH="$pnpm_bin:$link_home/pnpm-home:/usr/local/bin:/usr/bin:/bin" \
  /home/agent/am/c/skills/agent-memory/scripts/setup.sh)
readlink -f "$link_home/pnpm-home/global/5/node_modules/my-claude-plugins"
```

```text
exit=0
agent-memory setup: linking repository package from /home/agent/am/c
agent-memory setup: this global link exposes three bins: arcp, agent-memory, sandbox-ctl
agent-memory setup: repository global link complete; bare command is on PATH
/home/agent/am/c
```

### link 异常不会冒称成功

以 `--version` 成功、`link --global` 返回 9 的隔离 pnpm fixture 运行：

```sh
HOME="$fail_home" PATH="/tmp/am-c-fake-pnpm:/usr/local/bin:/usr/bin:/bin" \
  skills/agent-memory/scripts/setup.sh
```

```text
exit=1
simulated pnpm link failure
agent-memory setup: pnpm global link failed; no CLI installation is being reported
```

### settings override 优先级

同时设置 `AGENT_MEMORY_SETTINGS` 与 `AGENT_MEMORY_HOME`，前者指向 malformed fixture：

```sh
env -i HOME="$override_root/home" \
  AGENT_MEMORY_HOME="$override_root/implicit" \
  AGENT_MEMORY_SETTINGS="$override_root/explicit/settings.json" \
  PATH="$py_bin" /bin/bash "$copy_root/agent-memory/scripts/setup.sh"
```

```text
exit=2
agent-memory setup: invalid settings at .../explicit/settings.json: invalid JSON ...
implicit_settings_exists=no
```

### 回归测试

```sh
python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py'
```

```text
Ran 37 tests in 2.209s
OK
exit=0
```

`bash -n skills/agent-memory/scripts/setup.sh` 与 `git diff --check` 均 exit 0、无输出。

## 未解决项

- 无 lane C blocker。
- setup 只完成健康空 registry；没有 binding/memory root 时 capture 仍不可用，这是既有合同且不在本 lane 扩展。
- 本 lane 不实现 shim 生命周期或自动损坏恢复；损坏 DB 仍需停止 writers 后将 DB/WAL/SHM 整组 quarantine，再显式 sync。

## HANDOFF

- Lane D 的持久文档应使用 skill-local Python 入口作为跨 runtime fallback，并明确需要完整 `scripts/` 目录；不要宣传单文件部署或假设 `CLAUDE_PLUGIN_ROOT` 是普通 shell 环境变量。
- 集成验收应在合并其他 lanes 后重跑 37 tests 和相同 bootstrap 状态矩阵；若 DB schema 在 lane A/B 变化，健康旧 index 仍不会被 setup 隐式 sync。
