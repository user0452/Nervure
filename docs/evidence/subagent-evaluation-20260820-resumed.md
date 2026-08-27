# Nervure Subagent / Explore A/B 正式评测（2026-08-20 恢复批次）

## 结论

本轮在冻结的 Nervure revision 上完成了 32 个正式 primary target trial，顺序为 A_OFF r1 → B_ON r1 → B_ON r2 → A_OFF r2，Harbor batch concurrency=4，A/B 没有重叠。经过预先约定的基础设施补跑，Phase 1 得到 27 个有效正常完成样本：A_OFF 12 个，B_ON 15 个；另有 13 次 Phase 1 provider/Harbor infrastructure error、3 次 Agent operational timeout，均未被插值进 A/B 有效分母。首次 B2 命令中的一个意外 partial 被取消并单独保留。

B_ON 的 Explore 自然采用率为 7/15（46.7%），r1 为 3/8，r2 为 4/7；task 级多数为 1/2，未观察到稳定的 2/2 采用。A_OFF 有效样本 correctness 为 12/12，B_ON 为 14/15；12 个 matched pairs 中 correctness 差异为 0。当前最可信的判断是 Subagent 更像 decomposition/context-isolation 机制，尚不足以宣称总成本、延迟或正确率提升。

Phase 1 达到 4 个 natural adoption 门槛后，额外完成了一次 4-task Phase 2 natural Explore batch：3/4 正常完成、1 次 provider wall-clock infrastructure error，仅 1 个 Explore adoption，未自然产生多个 Explore child；未为制造并行而修改 prompt。

## 冻结和环境

- Nervure revision：f0e9cec8ae103049efdb57ca082960f242d58c1f；工作树 dirty，冻结期间未改源代码。
- Harbor：v0.21.0，commit 64afbbcb62165950301e1a6407c729aa26d844ff。
- Provider/model：custom / mimo-v2.5-pro-1m；初始 health gate 连续 2/2 通过，正式批次后及补跑前 preflight 也观察为 PASS。
- Dataset：nervure-medium-coding，8 个固定 medium case；当前 python -m evals.cli validate-dataset = PASS。冻结前已有 buggy fixture 8/8 FAIL、Oracle 8/8 PASS、hidden verifier isolation PASS、instruction leak PASS。
- 正式输出目录：C:\Users\刘\Desktop\Nervure-Traces\subagent-evaluation-resumed-20260820-103205。

## 有效性与结果

| variant | primary target | effective valid | correctness PASS | correctness FAIL | primary timeout | primary infrastructure |
|---|---:|---:|---:|---:|---:|---:|
| A_OFF r1 | 8 | 6 | 6 | 0 | 2 | 0 |
| B_ON r1 | 8 | 8 | 7 | 1 | 0 | 2 |
| B_ON r2 | 8 | 7 | 7 | 0 | 1 | 2 |
| A_OFF r2 | 8 | 6 | 6 | 0 | 0 | 4 |

正式试验之外还运行了 11 个允许的 infrastructure rerun、4 个 Phase 2 trial。Phase 1 unresolved infrastructure target 为 A_OFF r2 的 taskflow_retry_state 和 route_astar_optimality，各两次补跑仍遇到 provider wall-clock deadline。B_ON r2 的 route_astar_optimality 是 AgentTimeout，按规则不重跑。A_OFF r1 的 xiangqi_flying_general 出现 reward=1 但 AgentTimeout，记录为 correctness PASS / operational FAIL。

12 个 task+repeat matched pairs 的主要方向见桌面 paired-analysis.json。B-A 平均/中位数如下：total model calls +0.92/+1.5，total uncached input +8,984.8/+5,619，main model calls -1.17/-0.5，main uncached input +4,066.9/-219.5，main read_file +0.08/+0.5，main glob -0.08/0，main grep +0.42/0；duration 平均 -119,563.8 ms、中位数 -84,917.9 ms，但 provider long-tail 和批次时间差使 latency 结论为 LOW。Parent 与 Total 必须分开解读：child overhead 不能从 parent 指标中省略。

## Adoption、Context 和权限

- B_ON 有效样本 15 个，其中 7 个自然调用 agent/Explore；首次 agent call 的 turn_count 分布、task 级 2/2/1/2/0/2 和 raw trace 路径见桌面 adoption.json。
- adopted child 全部为 read-only Explore；本轮结构化质量启发式为 5 个 REDUNDANT_DELEGATION、2 个 PARTIAL_REUSE。由于 child prompt/final text 在 trace 中脱敏，这些标签只表示 child 正常完成、返回 tool result 与 parent 文件重读关系，不能替代原始 trace 的语义审阅。
- adopted child scope 中 bash 尝试 1 次、edit/write=0、permission deny=1、tool errors=14（主要为 child 的 invalid_tool_input），7/7 正常完成并返回结构化结果；parent permission_wait 与 child read-only 边界按 session 分开统计。
- B 的 context_prepare 记录包含 system_prompt_hash、tool_schema_hash、message_count、tool_schema_count、estimated_tokens 和 child session identity；见 context-isolation.json。当前 paired sample 显示 B 并未稳定减少 parent 的 read/glob/grep，不能把“上下文隔离”写成“总 token 下降”。

## Phase 2、Fork/Background 与限制

Phase 2 没有自然出现多个 Explore child，因此结论为 multi-subagent natural adoption insufficient。Fork 与 background 的运行时语义沿用当前 revision 已有 targeted/integration evidence；本轮没有为了扩大 benchmark 重新烧 provider。

本轮刻意没有修改 Subagent runtime、prompt、task instruction、verifier、metrics analyzer、Memory、Compact、Fork、HITL 或其他架构，也没有强制模型调用 agent。

## 测试

- python -m evals.cli validate-dataset：PASS
- python -m pytest tests -q：751 passed in 19.80s
- python -m compileall -q core services infrastructure tools evals ui：PASS
- git diff --check：通过（仅保留既有 LF/CRLF 警告，无 whitespace error）

## 证据路径

桌面目录保存 manifest.json、freeze.json、provider-preflight.json、task-validation.json、all-trials.json、valid-trials.json、phase2-trials.json、infrastructure-invalid.json、adoption.json、paired-analysis.json、context-isolation.json、delegation-quality.json、permission-friction.json、timeout-analysis.json、final-metrics.json、final-report.md，以及所有 Harbor raw jobs/artifacts。上一轮 503 outage 的原始目录 C:\Users\刘\Desktop\Nervure-Traces\subagent-evaluation-20260820-070620 未覆盖。
