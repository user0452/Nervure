# Nervure Runtime Closure

本 ExecPlan 是活文档，按仓库根目录 `PLANS.md` 维护。目标是关闭现有运行时链路中的组合、恢复、状态和上下文缺口，不增加新的产品级能力。

## Purpose / Big Picture

完成后，生产 `build_runtime()` 能真实构建；所有会修改工作区的主代理和子代理工具在执行前共用同一个 checkpoint store；`/undo` 只恢复当前会话的最新有效检查点并刷新文件缓存；`/resume` 明确区分“恢复对话”和“恢复可安全继续执行的状态”；运行结束能区分完成、部分、失败和取消；最终模型上下文在附件、记忆、系统提示和工具 schema 全部投影后再做一次预算判断；后台子代理保留 profile，并在终端结束时立即提示，同时继续保留下一轮通知。

## Progress

- [x] (2026-08-28) 阅读仓库指引、架构文档、相关设计文档、活跃计划和技术债。
- [x] (2026-08-28) 在隔离的干净 clone 上建立基线：`uv run python -m pytest tests -q`，结果为 `758 passed`。
- [x] (2026-08-28) 确认生产组合的两个直接缺陷：`AttachmentCollector` 收到不存在的 `checkpoint_store` 参数；普通 `RegistryToolExecutor` 没有收到已创建的共享 `CheckpointStore`。
- [x] (2026-08-28) 修复主代理、会话重建、模型重建和子代理的 checkpoint 组合，并增加生产构建 smoke test。
- [x] (2026-08-28) 实现 `/undo` 和 session validation metadata，完成安全恢复语义。
- [x] (2026-08-28) 补齐运行状态、取消、截断恢复耗尽、最终上下文预算和后台任务通知语义。
- [x] (2026-08-28) 完成聚焦测试、一次最终完整测试和静态边界检查：`766 passed`，`compileall`、import boundaries、`git diff --check` 均通过。
- [x] (2026-08-28) 完成 closure follow-up：persistent TTY 保留 Error/PARTIAL notice；final budget 改为 raw compact + 重建后拒绝；runtime replacement 重绑 UI bridge；异步退出持久化状态；resume 拒绝 workspace 外 metadata 路径。
- [x] (2026-08-28) follow-up 验证完成：`771 passed in 15.12s`，`compileall` 与 `git diff --check` 通过。

## Surprises & Discoveries

- `CheckpointStore.list()` 当前按随机 UUID 目录名倒序，而不是按 manifest 时间排序，因此“latest”语义并不成立。
- executor 已有修改前 checkpoint 机制，但创建失败后仍继续调用写工具，无法保证“先快照再修改”。
- 当前上下文 preparer 链在附件和长期记忆投影之前做自动压缩，最终发给模型的完整 snapshot 没有预算闭环。
- Ctrl+C 取消目前被写成 `USER_INTERRUPT` 交互暂停，混淆了取消和 HITL。
- persistent TTY 主路径没有复用旧 `stream_view` 的 `error_text` 渲染，因此 reducer 已记录的 Error/PARTIAL 提示仍会丢失。
- runtime replacement 只刷新 terminal completer，没有迁移 `BackgroundTaskManager` notifier；`consume_events()` 还在清理 active state 后错误地返回 `None`。

## Decision Log

- 2026-08-28：复用现有 `CheckpointStore` 和 executor 前置检查点逻辑；子代理只接收同一 store，并通过父 session id 归档，不复制 checkpoint 实现。
- 2026-08-28：`/undo` 作为显式命令即用户确认；恢复前仍须用当前 guard 重新验证 manifest 中每个路径，恢复后使对应 `FileStateCache` 条目失效。
- 2026-08-28：`/resume` 始终可以恢复 transcript；只有 workspace、git、文件哈希、指令、工具配置和权限模式均匹配时才标记 `SAFE_RESUME`。工作区分歧会清空旧文件状态，配置分歧标记 `STALE_CONTEXT`。
- 2026-08-28：扩展现有 `RuntimeState` 的 `RunStatus`，不新建第二套状态机；transition reason 继续单独表示控制流原因。
- 2026-08-28：最终预算检查由 `ContextEngine` 在完整 snapshot 上调用现有 compaction service；若压缩了底层 transcript，只允许重建一次，临时附件和记忆投影不写回消息存储。
- 2026-08-28：follow-up 将完整 snapshot 限定为预算判断输入；摘要仅基于 raw transcript，重建后必须再次通过同一阈值，压缩失败或重建仍超量均不得继续普通 Provider 调用。
- 2026-08-28：遵照用户要求避免过度测试，只为每个关键闭环增加一个直接回归测试；完整测试仅在基线和最终各运行一次。

## Context and Orientation

生产组合入口在 `ui/cli/app.py`，不可变重建逻辑在 `ui/cli/types.py`。工具执行和 checkpoint 前置逻辑在 `services/tools/executor.py`、`services/checkpoints/store.py`；子代理执行器在 `services/subagents/runner.py`。恢复入口在 `ui/cli/resume.py` 和 slash command 分发器。运行状态由 `core/runtime_state.py` 承载，主循环在 `core/loop.py`。最终上下文由 `core/context_engine.py` 组装，现有压缩服务在 `services/context/compaction.py`。后台任务生命周期在 `services/background_tasks/manager.py`，agent 工具在 `tools/agent/tool.py`。

## Plan of Work

先修生产组合，使主 executor、模型重建 executor 和子代理 executor 都引用同一个 checkpoint store，并阻止 checkpoint 失败后的实际修改。随后为 store 提供按时间选择最新有效检查点和只读 manifest 预览，`/undo` 用 guard 验证、恢复并失效缓存。

接着增加版本化 `session_state.json`。状态只保存轻量验证信息，不序列化运行时对象：workspace、git HEAD、指令指纹、工具/模型配置指纹、已读/已改文件哈希和权限模式。resume 先分类再构建新 runtime，并在 UI 中说明是否只是恢复 transcript。

最后补齐 `RunStatus`、取消和截断部分完成语义；把最终预算检查放到完整 snapshot 后；透传后台 profile，并给终端追加即时生命周期通知而不消费下一轮 attachment 通知。

## Concrete Steps

所有命令从仓库根目录执行。实现期间只运行受影响测试文件，例如：

    uv run python -m pytest tests/test_cli_runtime_composition.py tests/test_cli_commands.py -q
    uv run python -m pytest tests/test_resume.py tests/test_loop.py tests/test_context_engine.py -q
    uv run python -m pytest tests/test_background_agent_tool.py tests/test_background_tasks.py -q

最终只再运行一次：

    uv run python -m pytest tests -q
    uv run python -m compileall core services tools ui
    git diff --check

## Validation and Acceptance

生产 smoke test 必须调用真实 `build_runtime()`，只 mock 外部 provider/MCP 边界，并证明可执行写工具且当前 session 出现 checkpoint。`/undo` 测试证明最新检查点被恢复、路径经过 guard、文件缓存失效且结果报告文件。resume 测试覆盖三种分类并证明 diverged 不沿用旧文件缓存。loop 测试证明取消不是 HITL、截断耗尽为 PARTIAL 且保留文本。context 测试证明完整投影超预算后最多重建一次。后台测试证明 profile 透传、即时通知和下一轮通知可同时存在。

## Idempotence and Recovery

所有持久状态使用现有 session 目录和原子 JSON 写入方式。checkpoint 恢复只触及 manifest 已列出的、且当前 guard 允许的路径；失败前不调用写工具。测试使用临时工作区，不删除用户目录。若实现中发现范围外架构问题，只记录为剩余缺口，不扩大本 pass。

## Outcomes & Retrospective

本轮关闭了生产 `build_runtime()` 的真实构造失败，并把主代理、session/model 重建和子代理统一到同一个 checkpoint store。checkpoint 现在按 manifest 时间选择最新有效项；创建失败会阻止修改；`/undo` 在恢复前重新验证 sandbox 路径、在恢复后使文件缓存失效并报告文件。

session-local `session_state.json` 记录 workspace、Git HEAD、指令指纹、工具/模型配置指纹、已读/已改文件哈希和权限模式。`/resume` 仍恢复 transcript，但显式分类为 `SAFE_RESUME`、`STALE_CONTEXT` 或 `WORKSPACE_DIVERGED`；workspace diverged 时不沿用旧文件状态。

`RuntimeState` 现有状态对象增加 `IDLE / RUNNING / WAITING_USER / COMPLETED / PARTIAL / FAILED / CANCELLED`，取消不再伪装成 HITL。max-output 恢复耗尽会保留最终文本、标记 `PARTIAL` 并在 TTY/batch 显示提示。完整 context snapshot 在附件、记忆、system prompt 和 tool schemas 投影后再检查预算，只压缩底层 transcript 并最多重建一次。

后台 agent 已透传 profile；terminal state 会立即追加提示，同时不消费下一轮 background attachment notification，也不自动唤醒父 agent。

follow-up 进一步把 reducer 的 `error_text` 接入 persistent TTY active view 和 transcript notice，修正 `consume_events()` 的终态返回；runtime 替换统一重绑后台 notifier，退出统一解绑，避免未配置启动后 `/connect` 丢失即时通知，也避免 `/exit` 清空 runtime 后的尾部解引用。异步 shutdown 现在与同步路径一致地保存 session validation state；resume 会拒绝 metadata 中 workspace 外的绝对路径或相对逃逸路径。

最终上下文预算 follow-up 不再用包含 Memory/Attachment 的完整投影生成摘要，而是只压缩 raw transcript 的廉价投影；随后重新走完整投影，并在任何 Provider 调用前再次校验。仍超预算时返回明确的上下文错误。

最终验证：基线 `758 passed`；实现后 `766 passed in 17.12s`；`uv run python -m compileall -q core services infrastructure tools ui`、`tests/test_import_boundaries.py` 和 `git diff --check` 通过。生产 smoke test 使用真实 `build_runtime()`，按要求只 mock 外部 provider/MCP 边界。本 pass 未执行真实 provider 或真实 MCP server 的联网端到端测试，这是有意保留的外部验证边界，不是本地 runtime closure 缺口。

## Interfaces and Dependencies

新增接口限制为：`RuntimeState.status: RunStatus`；`CheckpointStore.latest()`/manifest 预览；轻量 session validation store；`ContextEngine` 可选最终预算管理器；`BackgroundTaskManager` 可选终端通知 callback。不得引入新的第三方依赖。
