# Compaction Architecture

本文描述 `services/compaction/` 的架构边界：tool result 预算、micro/auto/manual/reactive 压缩和 Full Compact。它作为 `ContextEngine` preparer 链的最内层接入，不进入 `core/loop.py` 的具体分支。底层消息存储见 `context-architecture.md`，长期记忆选择见 `memory-architecture.md`，共享工具结果存储见 `utils/toolResultStorage`。

## 文件职责

| 文件 | 职责 |
|:---|:---|
| `service.py` | `ContextCompactionService`：cheap pipeline + auto/manual/reactive compact |
| `token_estimator.py` | 保守本地 token 估算（文本 ≈ ceil(len/3)） |
| `types.py` | `CompactionConfig`、`CompactionTrigger`、`CompactionResult` |

## 接口设计

### ContextCompactionService

```python
async def prepare(messages, state) -> PreparedContext          # preparer 入口
async def prepare_for_model(messages, state) -> CompactionResult # cheap pipeline，不改写 store
async def maybe_auto_compact(messages, state) -> CompactionResult | None
async def manual_compact(state, *, focus=None) -> CompactionResult
async def reactive_compact(state, *, error: ProviderError) -> CompactionResult
def bind_runtime(*, message_store=None, result_store=None, model_client=None, current_model_context=None) -> None
```

它实现 `core` 期望的 `ReactiveCompactor` 协议。

### CompactionConfig（关键默认值）

| 字段 | 默认 |
|:---|:---|
| `context_window_tokens` | 128_000 |
| `compact_trigger_ratio` / `compact_summary_reserve_ratio` / `compact_safety_buffer_ratio` | 0.80 / 0.10 / 0.05 |
| `compact_recent_tail_ratio` | 0.08 |
| `recent_tail_min_tokens` / `recent_tail_max_tokens` | 4_000 / 32_000 |
| `tool_result_hard_cap_tokens` | 16_000（单条 tool result 独立硬顶）|
| `tool_result_remaining_ratio` | 0.25 |
| `tool_result_preview_chars` | 4_000 |
| `tool_result_budget_chars`（legacy 字符兜底，正常决策不再依赖）| 200_000 |
| `microcompact_keep_recent` | 5 |
| `snip_max_messages` | 80 |
| `max_consecutive_auto_compact_failures` | 3 |
| `max_reactive_compact_retries` | 1 |

派生：`compact_trigger_tokens = 102_400`，`compact_summary_reserve_tokens = 12_800`，`compact_safety_buffer_tokens = 6_400`，`recent_tail_budget_tokens = 10_240`，`auto_compact_threshold_tokens` 兼容别名指向 `compact_trigger_tokens`。

### CompactionTrigger / CompactionResult

`CompactionTrigger`：`MICRO`、`AUTO_FULL`、`MANUAL`、`REACTIVE`。`CompactionResult`：`trigger`、`messages`、`token_before`、`token_after`、`transcript_refs`、`metadata`。

## 核心数据流

```mermaid
flowchart TD
  Prep["prepare(messages, state)"] --> Cheap["prepare_for_model: cheap pipeline"]
  Cheap --> Budget["1. tool result budget: 动态 token 预算 → ToolResultStorage + 引用"]
  Budget --> Snip["2. snip: ContextProjector(max_messages=80)"]
  Snip --> Micro["3. microcompact: 旧 tool result → 占位符"]
  Micro --> Est["token 估算 → state.metadata['last_compaction']"]
  Est --> Check{"token_after ≥ 93K 且非 compact 子 agent?"}
  Check -->|否| Out["PreparedContext"]
  Check -->|是| Auto["maybe_auto_compact"]
  Auto --> Full["full compact (same model snapshot)"]
  Auto --> Out
```

## 关键机制

### Cheap pipeline（投影，不改写 store）

`prepare_for_model` 顺序：tool result 动态预算 → snip（`ContextProjector` 滑窗，最多 80 条）→ microcompact（旧的非 stored tool result content 替换为占位符）→ token 估算。这一阶段只投影，不调用 `replace_messages_for_compaction`。

Tool result 动态预算是**单条结果的局部保护**，与全局 Final Context Budget / Full Compact 职责分离。对每条 `tool_result` 按消息顺序做 sequential projection：维护已决策前缀的投影 token 累计 `prefix_projected_tokens`，并加上 `CurrentModelContext` 中上一次请求快照的固定前缀（system prompt、tool schemas、hints；其旧消息刻意不计，避免与当前消息链重复计算），得到 `context_tokens_without_result`；随后

- `remaining_tokens = auto_compact_threshold_tokens - context_tokens_without_result`（不小于 0）；
- `dynamic_budget_tokens = min(tool_result_hard_cap_tokens, int(remaining_tokens * tool_result_remaining_ratio))`；
- 当结果估算 token 大于动态预算时，完整结果写入共享 `ToolResultStorage`，模型只见 ref + preview；否则原样内联。

候选结果自身的体积不计入自己的 baseline；已内联的大结果继续占用后续结果的预算，已外置结果只按 ref+preview 的实际投影体积入账，因此多条连续大结果不可能各自重复吃掉 25% 的原始剩余空间。context 越满，允许内联的结果越小；预算同时随 `context_window_tokens` 缩放。`tool_result_budget_chars` 仅作为显式配置的 legacy 字符兜底（默认配置下 token 规则总是先生效）。`compact_result_budget` trace 事件为每条候选记录 `result_estimated_tokens` / `dynamic_budget_tokens` / `context_tokens_without_result` / `remaining_tokens` / `hard_cap_tokens` / `externalized`，不记录结果内容。

### Destructive compact（改写活动链）

`maybe_auto_compact` / `manual_compact` / `reactive_compact` 才调用 `replace_messages_for_compaction`。full compact 产物为 `(boundary_user_msg, summary_user_msg, *tail)`：boundary metadata 含 `is_compact_boundary`、`compact_trigger`、`compact_source`；summary metadata 含 `is_compact_summary`。摘要由 fork subagent 生成。`compact_request` 只表示一次请求已发起；真实 provider usage 与 duration 以 `compact_completed` 为准，`compact_failed` 单独记录失败，避免把请求前估算误当成实际 usage。

### 触发与断路器

`prepare()` 先跑 cheap pipeline，若 `token_after ≥ auto_compact_threshold`（默认 102,400）且当前不是 compact 子 agent 调用，则尝试 `maybe_auto_compact`。连续 auto compact 失败 ≥ 3 次（`max_consecutive_auto_compact_failures`）后跳过自动压缩。reactive compact 由 loop 在 `context_limit_exceeded` 时触发（最多 `max_reactive_compact_retries` 次）。

主 CLI 的 `ContextEngine` 还会在附件、相关记忆、system prompt 和 tool schemas 全部投影后调用 `ensure_final_context_budget()`。完整 snapshot 只用于预算判断；超过阈值时，service 基于 raw transcript 的廉价投影生成摘要并改写底层消息链，然后由 engine 重新执行一次完整投影。重建后的 snapshot 会再次校验，仍超过阈值时直接报告 `context_limit_exceeded`，不会继续交给普通 Provider 调用。临时附件和记忆投影不会进入摘要或 compact 后的持久消息链。

### Full Compact 与稳定前缀

`ContextCompactionService` 只负责 cheap pipeline 和 Full Compact；长期记忆 selector/extraction 属于 `services/memory/`，不再由 compaction service 持有 session-memory store。Full Compact 首选 `CurrentModelContext.snapshot_copy()` 得到最近一次实际发送给 provider 的 immutable snapshot；没有可复用 snapshot 时才安全回退到当前消息的最小投影。compact instruction 只追加在父 snapshot 消息末尾，保留 system prompt、tool schema 和消息顺序以争取 prompt-cache 命中。

`compact_request` 是请求前的形状/预算事实，不能当作 provider usage；`compact_completed` 才记录实际 input/cache-read/uncached/output/reasoning/duration，`compact_failed` 单独记录失败。

Full Compact 的 prompt-cache 验收还要求保留父模型请求的 immutable snapshot：system prompt、tool schema 与 parent message 前缀保持一致，compact instruction 只追加在末尾。一次真实 `mimo-v2.5-pro-1m` 验证记录 input `14396`、cache-read `14336`、uncached `60`、output `404`、duration `13688.006 ms`；详见 `docs/evidence/v1-evidence.md`。该数值是单次 live evidence，不是平均性能承诺。

### ToolResultStorage

路径 `<session_dir>/tool-results/<result_id>.txt`，`persist_tool_result(...) -> StoredToolResultRef`，`format_model_reference(ref, preview)` 生成模型可见引用。该实现位于 `utils/toolResultStorage`，同时服务 transcript 外置、compaction 层动态 token 预算（legacy 字符兜底）和 executor `ToolResultPolicy` 预算；compaction 只消费 ref 和模型引用文本，不拥有通用存储实现。

### PreCompact hook

`PRE_COMPACT` hook 返回的 `metadata["summary_instructions"]` 可注入 compact prompt；`POST_COMPACT` / `COMPACT_FAILED` 为观察事件。详见 `hook-architecture.md`。

## 持久化路径

- shared tool result storage：`.nervure/sessions/<session_id>/tool-results/<result_id>.txt`（旧 `.onecode/sessions/` 结果可读取）
