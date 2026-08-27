# Nervure V1 Freeze Report

**NERVURE V1 FREEZE: VALIDATED**

日期：2026-08-19
当前 Git 基线：`f0e9cec8ae103049efdb57ca082960f242d58c1f`（工作树保留用户既有 dirty 修改；本轮未执行 reset、checkout、clean、revert 或提交）

## 冻结结论

V1 runtime、工具/权限边界、`.nervure` 状态路径迁移、provider catalog、CLI capture、FileStateCache 和既有证据包均已完成收尾。全量测试已达到稳定全绿，可以正式标记 Nervure V1 freeze。

本报告只冻结当前 V1 行为和工程契约，不代表后续方向性实验已经完成。repo_map 样本扩大、symbol_search 性能边际和模型语义停机仍是非阻塞观察项。

## 本轮完成项

### 1. Root-worktree sandbox

- 修复 `services/guard/boundary.py::_is_unsafe_root_worktree()`：Windows `C:\` 等盘符根路径现在正确识别，POSIX `/` 同样处理。
- 失败的 worktree 查询不能再把整个盘符纳入 `inside_worktree` allow；外部路径仍返回 `ask`。
- 回归覆盖：通用 filesystem root、Windows drive root、root-worktree 外部文件。

### 2. `.nervure` / `.onecode` 迁移

- 当前产品契约测试统一使用 `.nervure`：session transcript、background output、resume suggestions、memory extraction、tool result、trace/error sink 等。
- 明确保留并单独测试 legacy compatibility：
  - `.onecode/sessions/*/messages.jsonl` 可被 resume 扫描和恢复；
  - `.onecode/memory/*.md` 可在既有 store/job 明确选定时继续受限写入；
  - legacy session tool-results 仍受保护；
  - 更早的 `.onecode/<session-id>/messages.jsonl`（缺少 `sessions/` 层级）仍按产品契约拒绝。
- `ui/cli/commands.py` 的 `/resume` suggestions 现在同时扫描 primary 与 legacy session roots。

### 3. Provider catalog

- 恢复设计、测试和文档已承诺的 `claude-openai-compatible`。
- 它是用户提供的 OpenAI-compatible gateway：`requires_base_url=True`、无默认 base URL，不假装 Claude 原生 Anthropic endpoint 兼容 Chat Completions。
- `/connect` 顺序、配置解析、catalog 测试和 provider 架构文档已同步。

### 4. Bash、ANSI 与 FileStateCache

- Bash prompt 不再用旧的 `Tree-sitter` 文本断言；现在验证实际 descriptor 语义和 `.nervure/sessions/.../background-tasks` 路径。
- Bash prompt 已更新为当前 `.nervure` 输出位置，并注明 legacy session 读取兼容。
- 两个 ANSI 失败确认是 capture fixture 的 `Rich Console(no_color=True)`，不是 UI 回归；fixture 改为显式 `no_color=False`，生产 UI 代码未改动。
- `FileStateCache.changed_text_files()` 改为直接比较当前文本；mtime 保留为 metadata，不再作为唯一变更判断。新增同大小、恢复相同 mtime 的快速编辑测试，不使用 sleep。

## 关键修改位置

- Sandbox：`services/guard/boundary.py`、`tests/test_path_sandbox_guard.py`
- 迁移/权限：`services/memory/paths.py`、`services/permissions/policy.py`、`ui/cli/commands.py`、相关 migration tests
- Provider：`infrastructure/providers/catalog.py`、`tests/test_openai_compatible_provider.py`、`docs/design-docs/model-provider-architecture.md`
- Bash/ANSI：`tools/bash/prompt.py`、`tests/test_bash_tool.py`、`tests/test_cli_terminal.py`
- FileStateCache：`services/tools/file_state.py`、`tests/test_file_tools_guard.py`
- 文档与债务：`docs/design-docs/` 中状态路径说明、`docs/tech-debt/tech-debt-tracker.md`

## 验证结果

- 本轮 targeted suite：`187 passed`
- import-boundary/runtime/memory/task 补充 suite：`18 passed`
- 编译检查：`python -m compileall -q core services infrastructure tools evals` 通过
- 差异检查：`git diff --check` 通过（仅 Git 的 LF→CRLF 提示）
- 全量：`python -m pytest tests -q` → `744 passed`
- 独立低输出复核：`python -m pytest tests -q --tb=no` → `744 passed`
- 另一次顺序复跑首轮同样为 `744 passed`

## 既有证据与模型调用边界

- Full Compact 真实证据仍为：`mimo-v2.5-pro-1m`，input `14396`、cache-read `14336`、uncached `60`、output `404`、reasoning `0`、duration `13688.006 ms`，cache-read ratio 约 `99.583%`。
- repo_map sanity：4 个有效 trial，adoption `1/4`，仅作为方向性信号。
- symbol_search exact probe：4/4 adoption，correctness 无回归；大样本性能仍未声称。
- 本轮没有重新运行 Harbor benchmark，没有调用真实模型，没有新增模型 token/费用；引用的历史 Full Compact harness 共 6 次 provider call，成功尝试本身 2 次。
- 本轮没有开始 12-case 批量评测，也没有改变 Memory、Compact 控制流、HITL、Fork 或 Subagent 语义。

## 冻结后的非阻塞观察项

1. 扩大 repo_map orientation 样本，确认 adoption 和成本收益。
2. 在更大样本上验证 symbol_search latency/performance margin。
3. 观察 300 秒 model-call wall-clock deadline 对持续探索型模型行为的影响；deadline 限制调用生命周期，但不判断语义完成。

以上观察项不阻塞当前 V1 freeze。
