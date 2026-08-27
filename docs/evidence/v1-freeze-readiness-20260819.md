# Nervure V1 Freeze Readiness Report

日期：2026-08-19
范围：V1 freeze 前综合收尾及其后置回归闭环；未重跑 Harbor benchmark，未调用真实模型。本文件的最终状态由同日的 `v1-freeze-report-20260819.md` 汇总。

## 结论

运行时与证据包已正式达到 V1 freeze 条件：搜索噪声、过时 search 测试、Full Compact metrics 口径、工具生命周期去重、历史 timeout 诊断、整次模型调用墙钟截止，以及本轮 root-worktree、迁移契约、provider catalog、Bash prompt、ANSI capture 和 FileStateCache 收尾均已完成。全量 pytest 已连续验证 `744 passed`，正式冻结报告见 `docs/evidence/v1-freeze-report-20260819.md`。

## 本轮修改

- `tools/grep/tool.py`：嵌套排除改为 `!**/<dir>/**`；显式 excluded path 保留；默认排除 `.cache` 而不是宽泛 `cache`。
- `tools/glob/tool.py`：保持显式根可读并用 `os.walk` 剪枝；同样不排除普通 `cache` 目录。
- `tests/test_search_tools.py`：prompt 断言改为当前 `Purpose:` 格式，并补 nested/explicit/hidden-config 覆盖。
- `services/model/deadline.py`：provider-neutral whole-call deadline；默认 `model_call_timeout_seconds=300`。
- `infrastructure/config/env.py`、`.env.example`、`services/model/types.py`：独立配置、正数校验、timeout metadata 和安全消息。
- `infrastructure/providers/chat_completions.py`：所有 OpenAI-compatible streaming model call 使用 deadline；底层 iterator 在超时/取消时关闭。
- `services/observability/trace.py`、`core/loop.py`：timeout/deadline/elapsed/partial progress 进入 span/event（只记计数，不记超时正文）。
- `tests/test_model_call_deadline.py`：7 项 timeout/config/trace/adapter/retry 测试。
- `services/guard/boundary.py`、`tests/test_path_sandbox_guard.py`：拒绝 filesystem root worktree，并覆盖 Windows drive-root 回归。
- `infrastructure/providers/catalog.py`、`tests/test_openai_compatible_provider.py`：恢复 `claude-openai-compatible`，要求显式 base URL。
- `services/memory/paths.py`、`services/permissions/policy.py`、`ui/cli/commands.py`：统一 `.nervure` 主路径并保留 `.onecode` session/memory 兼容。
- `services/tools/file_state.py`、`tests/test_file_tools_guard.py`：内容级变更检测，消除 mtime 精度依赖。
- `tools/bash/prompt.py`、`tests/test_bash_tool.py`、`tests/test_cli_terminal.py`：清理 stale prompt assertion，修正 ANSI capture fixture。
- 文档：`docs/design-docs/{compaction,builtin-tools,tool-runtime,observability,model-provider}-architecture.md`、`evals/README.md`、`docs/tech-debt/tech-debt-tracker.md`。
- 证据：`docs/evidence/v1-metrics.json`、`v1-evidence.md`、`timeout-analysis-20260819.md`。

## 实际 timeout 配置

`timeout_seconds=60` 仍是单次 HTTP transport 连接/读取超时；新增 `model_call_timeout_seconds=300` 限制完整 provider stream 生命周期。它覆盖历史 200–400 秒级慢调用中的 300 秒以内部分；超过 300 秒会以不可重试 `ProviderError(error_type="timeout_error")` 结束，记录 `wall_clock_deadline_seconds`、`elapsed_ms`、`partial_text_chars`、`partial_reasoning_chars`、`partial_tool_call_count` 和 `partial_output_visible`。该机制不改变正常完成、reactive compact（仍只由 context-limit 触发）或 max-output recovery。

## Full Compact cache evidence

一次真实 `custom / mimo-v2.5-pro-1m` 成功请求：input `14396`、cache-read `14336`、uncached `60`、output `404`、reasoning `0`、duration `13688.006 ms`，cache-read ratio `99.583%`；system/tools 与 180 条 parent message 前缀一致，compact instruction 第 181 条追加。来源与限制见 `docs/evidence/v1-metrics.json`。

## 验证

- 搜索/模型/compaction/eval/understanding/registry/observability 定向集合：`102 passed`。
- 本轮迁移/sandbox/provider/Bash/ANSI/FileStateCache targeted suite：`187 passed`。
- OpenAI-compatible provider（含恢复后的 catalog 契约）：全量纳入通过。
- `python -m compileall -q core services infrastructure tools evals`：通过。
- `git diff --check`：通过（仅有 Git 的 LF→CRLF 提示）。
- 全量 `python -m pytest tests -q`：连续运行均为 `744 passed`。
- `python -m pytest tests -q --tb=no`：独立复核 `744 passed`。

原先的 10 个失败均已逐项核对并收敛：产品契约路径测试改为 `.nervure`，真正 legacy 行为保留专门测试；root-worktree、provider catalog、Bash prompt、ANSI capture 和 FileStateCache 均已有针对性修复。

## P0/P1 建议

- P0：无。模型 stream 的无界慢调用已有硬截止和可观测错误分类；root worktree 不再信任盘符根目录。
- P1：保持后续观察项，不阻塞 V1 freeze：扩大 repo_map orientation 样本、验证 symbol_search 性能 margin、评估模型在 deadline 前的语义停机。
- P1：repo_map 扩大 orientation 样本后再评估性能；当前 4-trial adoption `1/4` 只支持方向性信号。symbol_search exact probe adoption `4/4` 且 correctness 无回归，但性能 margin 仍未验证。

本轮没有开始 12-case 批量评测，没有重跑 Harbor，也没有修改 Memory、Compact 控制流、HITL、Fork 或 Subagent 语义；冻结标记仅针对当前 V1 runtime、测试和证据契约。
