# Nervure V1 Subagent / Explore 评测记录（2026-08-20）

## 结论

本轮没有形成有效的 Subagent A/B 行为或性能结论。离线题库验收、容器隔离、OFF 开关、指标 lineage 拆分和本地回归均通过；真实 Phase 1 只启动了 A（Subagent OFF）第一重复。8 个 trial 都在第一次 Agent 模型生命周期内连续收到 provider HTTP 503：`No available channel for model mimo-v2.5-pro-1m`，随后以 `RetryExhaustedError`/`NonZeroAgentExitCodeError` 结束。

按预先约定，provider 阻塞后没有启动 B、第二重复、Explore adoption 分析、Phase 2 并发 Explore 或新的真实模型重试。全部失败 trace 和 Harbor artifacts 保留在桌面输出目录。

## 冻结条件

- Harbor：`v0.21.0`，commit `64afbbcb62165950301e1a6407c729aa26d844ff`
- Nervure revision：`f0e9cec8ae103049efdb57ca082960f242d58c1f`
- 工作树：dirty；冻结时保留既有修改，未 reset/checkout/clean/revert
- 模型/provider：`mimo-v2.5-pro-1m` / `custom`
- 题库：`nervure-medium-coding`，8 个 medium case
- 计划顺序：`A_OFF_r1 → B_ON_r1 → B_ON_r2 → A_OFF_r2`
- 并发：Harbor `n-concurrent=4`；A/B 不重叠
- OFF：`NERVURE_DISABLE_SUBAGENT=1`；实现为不注册 `agent` descriptor，因此不出现在 schema 或动态 prompt

机器可读的冻结信息见桌面目录 `manifest.json`、`freeze.json`。

## 题库和 verifier 验收

| 项目 | 结果 |
| --- | --- |
| Dataset structural validation | PASS |
| buggy fixture verifier | 8/8 FAIL |
| Oracle solution verifier | 8/8 PASS |
| hidden verifier 出现在 Agent environment | 否 |
| instruction 泄漏 subagent/Explore/delegate/parallel | 否 |
| Harbor/Docker | Harbor 0.21.0、Docker Desktop/WSL2 可用 |

## A_OFF r1 真实结果

| 汇总 | 数值 |
| --- | ---: |
| trials | 8 |
| reward=1 | 0 |
| Harbor infrastructure errors | 8 |
| ATIF validation | 8/8 true（仅说明轨迹格式合法，不代表任务完成） |
| model_call lifecycle | 8 |
| provider attempts（每 trial 10 次 retry） | 80 |
| input/cache/uncached/output/reasoning tokens | 0 / 0 / 0 / 0 / 0 |
| tool calls | 0 |
| child agents | 0 |
| 平均 lifecycle duration | 186,458.953 ms |

每个 trial 都保留 `trace.jsonl`、`trajectory.json`、`nervure_metrics.json`、`atif_validation.json`、`git_diff.patch`、stdout/stderr 和 Harbor `result.json`。失败原因来自 trace/error log，而不是 hidden verifier：provider 返回 503，Agent 没有进入工具或 Subagent 决策阶段。

## 代码与指标适配

- `evals/metrics.py` 现在识别真实 `subagent_start`、`subagent_completed`、`subagent_error`，按 `agent tool span → child interaction span` lineage 拆分 `main_agent`、`child_agent`、`total`。
- `subagent` metrics 包含 child count、完成/错误、agent type、fork/non-fork、read-only、duration、model usage、tool calls/errors 和每 child 明细。
- `services/subagents/runner.py` 的完成/错误事件补充 child cache/uncached/reasoning/visible usage 和 model call count。
- `ui/cli/app.py` 增加 `NERVURE_DISABLE_SUBAGENT`/`NERVURE_DISABLE_AGENT_TOOL` OFF 开关；OFF 完全跳过 descriptor 注册。
- Harbor wrapper 转发上述开关；`evals/baseline.py` 将 `RetryExhaustedError` 等 provider outage 从 Agent failure 单独归类为 infrastructure error。
- 相关架构、observability、eval README 和 tech-debt 条目已同步。

## 本地验证

- Subagent/metrics/registry/Harbor targeted tests：`38 passed`
- Fork/background/registry/import 等 targeted 集合：`69 passed`
- 全量 pytest：`751 passed in 17.60s`
- `compileall core services infrastructure prompts evals tools ui utils`：PASS

## 未执行项目及原因

- B_ON 两次、A_OFF 第二次：未执行；provider 503 后不自动重跑
- Explore adoption、good/redundant/poor/permission-friction 分类：无有效 B trace，不能计算
- Main vs Child vs Total 的真实 ON 对比：无有效 ON 样本
- Phase 2（自然并发 Explore）：未执行；需先有至少 3 个正常 Explore adoption 样本
- 真实 Fork parent/child token-cache 对比：仅保留现有单元/静态 harness，不调用 provider
- 真实 background agent notification：仅通过现有 fake-model/unit harness，未进行新的 provider trial

## 输出目录

`C:\Users\刘\Desktop\Nervure-Traces\subagent-evaluation-20260820-070620`

其中 `jobs\subagent-a-off-r1` 是 Harbor 原始 job；`a-off-r1\trials.json` 和 `summary.json` 是不改写 raw trace 的汇总。后续恢复评测前，先确认 provider 对 `mimo-v2.5-pro-1m` 不再返回 `No available channel`，再按冻结 manifest 重新开始一轮完整 A/B；不要把本轮 A_OFF 的 0 reward 当作模型能力结果。
