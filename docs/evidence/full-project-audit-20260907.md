# Nervure 全项目审计、真实 API 验证与实习项目建设报告

审计日期：2026-09-07 UTC。本机采用 Asia/Shanghai，因此本轮原始运行目录使用 `20260908`；两者属于同一轮工作。

审计起点：`5ae62134b1356d855cc62af8575db8ff941eb925`。修复及最终离线验证版本：`bc43c1b7724efebcd6c9d6c77c28b8ef0a213321`。

工作目录：`D:/all-python/nervure`。原项目未修改；本轮不重写历史、不扩展产品功能、不改变仓库可见性。报告中的改进路线是建议，不代表已经实现。

## 1. 结论

**Nervure 已经是模块较完整的 coding agent 工程原型，适合继续做 Agent 应用开发、AI 后端方向的实习项目；当前最缺的是可信的任务效果证据和运行可靠性，而不是更多工具名字。**

本轮完成风险驱动的跨模块审查、9 类 bug 修复、全量离线回归和部分真实模型测试。测试从基线 **845 passed** 增至 **895 passed**。这说明已有用例及新增回归通过，不等于覆盖率 100%，也不等于全部 bug 清零。

真实模型不是 mock：7 种模块场景都取得了成功记录，涵盖流式响应、工具回填、记忆选择、全文压缩、Explore 子运行时、记忆提取、完整文件编辑及关闭。它们来自多轮迭代，合计 14 次场景尝试中有 7 次失败，不能写成“一次完整 7/7”或生产成功率。

最有价值的真实发现是长单轮任务压缩失效：原逻辑为保留用户目标而把已摘要的中间步骤也重新带回。修复后，同一合成样例的上下文估算从 **40,628 → 41,325** 的增长，变为 **40,628 → 10,755**。这是一条可讲清楚触发条件、错误原因、修复和回归的工程证据，不是通用 token 节省率。

**实习匹配判断：当前为 risky fit，方向匹配但证据与归属需要补强。** 对 Agent 应用、LLM 工程、AI 后端岗位，项目内容具有 strong fit 的方向潜力；对要求模型训练、Agentic RL、后训练算法研究的岗位，目前是 weak fit，没有训练或算法实验可以替代这些要求。由于没有具体 JD，这不是针对某个招聘岗位的录用判断。

建议定位为：**在既有开源基础上持续改造的 Python coding agent，重点研究长任务上下文治理、异步记忆生命周期和可复现实验。** 不建议定位为“从零独立原创的完整商业 coding 平台”。

## 2. 范围与证据等级

阅读范围包括主循环、模型适配、上下文、记忆、工具执行与权限、代码搜索、bash、计划、人机交互、子运行时、技能、MCP、后台任务、文件快照、CLI、可观测性、评测及工程文档。采用入口追踪、关键状态流阅读、已有测试审查、故障注入回归和有限真实测试，不是逐行形式化验证。

本轮统计了 `core/services/infrastructure/tools/prompts/ui/utils`：255 个 Python 文件、38,703 行，包含注释与空行，不包含评测任务 fixtures 或测试。`tests` 有 102 个测试文件。规模只用于解释维护成本，不用于证明质量。

| 证据等级 | 本报告含义 | 本轮例子 |
| --- | --- | --- |
| 已复现并修复 | 原实现失败，修复后确定性回归通过 | 会话落盘竞态、截断工具 JSON、压缩尾部 |
| 真实模块验证 | 实际发送模型请求，并由程序断言结果 | 文件 JSON 内容校验、记忆 watermark 推进 |
| 静态观察 | 源码或文档支持，但未做该场景端到端复现 | MCP 跨事件循环生命周期、进程树回收 |
| 历史证据 | 旧版本留下的实验，不代表当前结果 | 2026-08-17 的 12 题 baseline |
| 建设建议 | 为实习项目提出的下一步 | CI、隔离执行、配对消融实验 |

本轮最终命令均在 `D:/all-python/nervure` 执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q core services infrastructure tools prompts ui evals utils
.\.venv\Scripts\python.exe -m evals.cli validate-dataset
git diff --check
```

结果：**895 passed in 21.15s**；编译通过；`Dataset Validation PASS`；diff 空白检查通过。环境为 Windows、Python 3.11.15。本轮没有测 coverage 百分比，没有执行干净环境安装矩阵，没有跑 Harbor/Docker 完整任务或 SWE-bench。

## 3. 已修复的 Bug

下表“高/中”是本次风险排序，不是未经验证的 CVSS 分数。代码及测试均在修复提交中。

| 编号 / 风险 | 问题与影响 | 修复方式 | 回归依据 |
| --- | --- | --- | --- |
| B1 / 高 | transcript、trace、error sink 在锁外落盘，切换 session 可把旧数据写入新目录；transcript 打开文件失败还会丢队列 | session 切换与落盘共用 RLock；transcript 成功写入并关闭后才清队列，append/load 保持同一会话 | `test_audit_persistence_regressions.py`：线程事件控制竞态、文件打开失败后重试 |
| B2 / 高 | task ID 可携带 `../` 等路径，删除操作可能影响任务列表外文件 | ID 限定为安全单个路径组件；拒绝 Windows 盘符/ADS 等；resolve 后仍须是直接子路径 | 同文件：穿越参数拒绝、目标文件保留 |
| B3 / 高 | 流内 error、缺 finish_reason 的 EOF 被当成正常完成；截断工具 JSON 可能被解析 | 显式失败；输出截断交由已有恢复逻辑处理，且不附带悬空 tool_calls | `test_openai_compatible_provider_streaming.py` |
| B4 / 中 | headless 重复实现不完整的关闭流程；挂起、partial、无 completed 可能被算成功 | 统一调用 `await runtime.close()` 后导出；非成功完成抛错 | `test_audit_persistence_regressions.py`：关闭顺序与完成状态 |
| B5 / 高 | MCP 请求执行后响应丢失，无条件重试可能重复写入 | 已派发请求仅在 `readOnlyHint` 或 `idempotentHint` 为 true 时自动重试；其他返回 unknown outcome；发送前连接失败仍可重试 | `test_mcp_retry_safety.py` 与原 reconnect 测试 |
| B6 / 高 | 单个用户任务很长时，recent tail 为保留 user anchor 回退整轮，压缩后又带回已摘要内容 | 保留单个 user anchor 和最近、调用结果配对完整的 tail，跳过已摘要中间段 | `test_compaction_service.py` 新增长单轮回归；真实模型同样例复测 |
| B7 / 中 | HTTP/whole-call timeout 接受 NaN、Inf 或非正数，超时语义失效或异常 | 配置与 deadline 边界要求有限正数，正常默认值不变 | `test_audit_config_plan_regressions.py` |
| B8 / 中 | PlanFile 注释宣称原子写，实际直接覆盖；写失败破坏旧内容 | 同目录唯一临时文件写入后 replace，并清理临时文件 | 同文件：注入部分写失败，确认旧计划完整 |
| B9 / 中 | Ollama 声明无需 key，但运行时强制 key；chat/probe endpoint 与协议不一致，列表未用 native parser | 对齐 `/v1/chat/completions`、可选 Authorization 和 `/api/tags` 解析 | `test_ollama_runtime_contract.py` 三项契约回归；接口依据见 [S8] |

主要实现位置：

- [Transcript](D:/all-python/nervure/services/context/transcript.py)、[Trace sink](D:/all-python/nervure/services/observability/sinks.py)、[Error sink](D:/all-python/nervure/services/observability/error_log.py)。
- [Task store](D:/all-python/nervure/services/tasks/store.py)、[Plan store](D:/all-python/nervure/services/plans/store.py)、[Headless](D:/all-python/nervure/evals/headless.py)。
- [Chat adapter](D:/all-python/nervure/infrastructure/providers/chat_completions.py)、[Catalog](D:/all-python/nervure/infrastructure/providers/catalog.py)、[Model catalog](D:/all-python/nervure/infrastructure/providers/model_catalog.py)。
- [MCP manager](D:/all-python/nervure/services/mcp/manager.py)、[Compaction](D:/all-python/nervure/services/compaction/service.py)、[Config](D:/all-python/nervure/infrastructure/config/env.py)、[Deadline](D:/all-python/nervure/services/model/deadline.py)。

修复边界：文件锁不提供多进程事务或断电持久性；没有增加 fsync，JSONL 部分写失败仍不是严格事务。MCP 修复不提供跨服务 exactly-once，模型主动重试还需上层策略。Plan replace 也不是全项目多文件事务。Ollama 仅做契约测试，未运行真实 Ollama 服务。

## 4. 真实 API 测试

### 4.1 配置是否真实可用

通过既有配置解析器确认存在真实 API key，但没有将 key、base URL、个人会话或记忆写入报告。原配置为 `custom / mimo-v2.5-pro-1m`，连续两次最小请求返回 provider `server_error`，没有 usage。

认证后的模型发现成功，返回 `mimo-v2.5` 和 `mimo-v2.5-pro`，没有原 alias。本轮用测试进程的 `--model mimo-v2.5-pro` 继续验证，**未修改 `.env`**。两次错误不足以证明 alias 永久下线，但说明原配置在本轮不可用，不能把后续成功等同于原配置已恢复。

### 4.2 隔离和限额

新增 [live_modules.py](D:/all-python/nervure/evals/live_modules.py)，必须显式 `--allow-live`，使用临时工作目录、合成文件和空测试 home。模型只接触 read/edit/write 文件工具，不暴露 bash、外部 MCP 或任意 agent 工具。Explore 和记忆提取由测试程序直接调用受限子运行时；完整 runtime 场景禁用其他工具。

主模型、selector、compaction、子运行时共用计量客户端。每次网络调用前按 UTF-8 JSON payload 字节数、输出上限和 8,192 framing allowance 保守预留；单次运行最多 30 请求，单请求输出最多 4,096。失败不退还预留，流式累计 usage 仅取最后一份，重跑扣除所有历史预留。该方法是保守估算，不是服务商隐藏 usage 的数学上界保证，也没有给生产 runtime 新增统一预算功能。

这里的“隔离”是限制工具与数据范围，不是 OS sandbox。本轮未在宿主机启动允许任意命令的模型代理，也未执行模型生成的 Python。

### 4.3 成功记录与验证方式

| 场景 | 程序实际断言 | 成功所在运行 |
| --- | --- | --- |
| stream | 最终文本严格为 `NERVURE_OK` | corrected |
| tool_roundtrip | 模型发出真实 read_file；执行并回填；最终回答含文件内未知 marker | corrected |
| memory_selector | 两条记忆中仅选 `testing.md` | pro-full |
| full_compaction | 保留目标 marker 和 API 约束；压缩后估算小于压缩前 | compact-fixed |
| explore_subagent | 子运行时实际读文件，marker 正确，fixture SHA-256 不变 | pro-full |
| memory_extraction | 受限子运行时把规则写入 Markdown，成功推进 watermark | pro-full |
| runtime_file_edit_and_close | 完整 AgentLoop 把 retries 从 0 改为 3，timeout=30 保留；程序解析 JSON；会话关闭并落盘 | pro-full |

每行只证明对应合成场景，不证明多语言代码修复率、长期记忆召回率或复杂任务可靠性。这些成功记录来自迭代过程中的工作树，并非在最终提交上一次完成的全套真实回归；最终提交完成了全部离线回归。

### 4.4 不隐藏失败

全部 14 次场景尝试保留在脱敏聚合数据中，7 次通过、7 次失败：

1. 原模型 alias 的两次请求报 server_error。
2. 两次早期 echo 场景未达到严格输出断言。早期 user 消息只有 marker，最终改为明确要求精确回复；这不能用来推断一般指令遵循质量。
3. 一次工具回填测试错误地 await 了异步 iterator，是测试 harness 问题，已改为 async iteration。
4. 一次压缩 fixture 太短，完整保留最近尾部本来就可能合法，先修正了测试预期和样例规模。
5. 扩大 fixture 后仍增长，才确认 B6 为生产代码问题，增加确定性回归并做真实复测。

没有删除失败、隐藏重试成本或把测试工具 bug 冒充生产 bug。

### 4.5 Token 账本

| 运行后缀 | 请求 | 保守预留 | 已回传 input | 已回传 output | 已回传合计 | 无 usage 请求 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 初次 | 1 | 12,507 | 未知 | 未知 | 未知 | 1 |
| retry | 1 | 12,507 | 未知 | 未知 | 未知 | 1 |
| pro | 1 | 12,504 | 21 | 59 | 80 | 0 |
| pro-full | 16 | 287,500 | 24,801 | 3,255 | 28,056 | 0 |
| corrected | 4 | 174,594 | 20,741 | 663 | 21,404 | 0 |
| compact-fixed | 1 | 135,862 | 19,963 | 1,317 | 21,280 | 0 |
| 总计 | **24** | **635,474** | **65,526** | **5,294** | **70,820 + 两次未知** | **2** |

用户授权上限为 1,000,000 tokens；累计保守预留 635,474，剩余预留额度 364,526。22 次请求返回 usage，cache-read input 合计 34,240，已经计入 input，不能再加到总量。70,820 不是完整账单精确总额；两次无 usage 请求不能按零成本处理。没有为了用满预算追加低价值调用。

机器可读证据：[live-module-audit-20260907.json](D:/all-python/nervure/docs/evidence/live-module-audit-20260907.json)。原始目录仍在忽略的 `D:/all-python/nervure/outputs/` 下，不提交原始对话、会话文件或临时绝对路径。

## 5. 同类项目对照

以下依据本轮实际读取的官方 README、文档及部分源码，固定版本见文末 [S1]-[S8]。不是全量竞品安全审计，也没有复跑它们的 benchmark。借鉴建议是本报告判断，不能理解为相同设计一定带来更高任务成功率。

| 项目 | 官方资料中可确认的重点 | Nervure 的差异 | 最值得借鉴的部分 |
| --- | --- | --- | --- |
| Aider [S1] | repo map 用 tree-sitter tags、引用图/PageRank、缓存和 token 裁剪；有 Git、lint/test 工作流 | 本项目 repo_map/symbol_search 主要是 Python AST 清单与定位，不是同等引用图检索 | 做有对照的代码定位实验，检验图排序是否优于简单检索 |
| OpenHands SDK [S2] | Agent、Conversation、Tool、Workspace、事件和 Agent Server API 的明确边界；可用本地或 Docker/Kubernetes 工作区 | 本项目完整 composition 仍主要放在 CLI；无独立隔离执行环境契约 | 提炼最小 Python API，分离执行环境与界面 |
| OpenHands Agent Canvas [S3] | 当前主仓库承担控制界面，SDK/Server 和 automation 有各自仓库边界 | Nervure 是单仓库终端 runtime，不是相同规模的平台 | 学边界，不复制完整多端平台 |
| mini-swe-agent v2 [S4] | 简洁的 agent/model/environment 分工；源码有 step/cost/wall-time/格式错误限制和轨迹保存 | Nervure 能力更多、状态链更复杂，额外复杂度尚缺统一效果证据 | 作为最小强基线，同模型、同任务、同预算比较 |
| SWE-agent [S5] | ACI/工具与轨迹设计有研究价值；官方说明主要开发已转向 mini | 不宜把较早的复杂架构直接当新增功能清单 | 学工具反馈格式和失败轨迹分析，实验基线优先 mini |
| Cline [S6] | 当前覆盖 CLI、SDK、IDE；diff/checkpoint、诊断、Plan/Act、rules/skills、MCP 组成用户工作流 | Nervure 有相似组件，但“看差异、验证、撤销、再执行”的整体体验和证据较弱 | 打磨可靠的编辑验证闭环，不急着复制所有客户端 |
| OpenCode [S7] | build/plan、子 agent、权限配置；LSP 可提供诊断 | Nervure deny-first 和它的 last-matching-wins 语义不同，不能照搬配置 | 先可靠执行 lint/typecheck/test，再决定是否需要 LSP |

几个容易过时的结论已纠正：OpenHands 当前应分别看 SDK 与 Agent Canvas；Cline 不再只是 VS Code 插件，其 JetBrains 客户端官方说明未开源，不能称全部客户端开源；OpenCode 本轮文档明确 LSP **默认关闭**，并提示资源开销和陈旧诊断风险。不能把“补 LSP”当成必然优先项。[S2][S3][S6][S7]

OpenCode 的 `.env` 默认拒读、外部目录及连续 3 次相同工具调用默认询问，可作为防误操作参考；但其规则最后匹配生效，不等于 Nervure 的全局 deny 优先。迁移行为必须逐例定义并测试。[S7]

**比较结论：Nervure 的亮点更适合放在上下文和记忆治理，以及这些机制与运行时生命周期的连接上。** 它不必在界面数量上追赶成熟平台，但需要回答“同样任务和预算下，这些模块有什么可测收益，失败时是否可恢复”。

## 6. 逐模块分析

本节优先级：P0 为可靠演示/无人值守前的前置条件；P1 为实习项目核心证据；P2 为有证据后再扩展。未标注“已复现”的风险属于静态观察或待验证项，不能当成本轮已确认并修好的 bug。

### 6.1 核心循环与状态机

证据：[loop.py](D:/all-python/nervure/core/loop.py)、[context_engine.py](D:/all-python/nervure/core/context_engine.py)、[transitions.py](D:/all-python/nervure/core/transitions.py)。

已有 provider/tool 抽象、流事件、turn 状态、恢复及 transition，复杂功能多由服务接入，这是可继续维护的基础。本轮真实文件编辑验证了主循环的一条正常路径。

不足是大量 metadata 字符串和跨服务隐式约定，复杂度主要藏在状态组合，而不是主循环行数。建议 P1 补终态不变量、取消/挂起/恢复事件序列测试，并使错误结果可区分可重试、未知副作用和需用户介入；不要为“框架先进”重写成另一套 agent 框架。

### 6.2 Provider、配置与模型发现

证据：[providers](D:/all-python/nervure/infrastructure/providers/chat_completions.py)、[env.py](D:/all-python/nervure/infrastructure/config/env.py)、[model_catalog.py](D:/all-python/nervure/infrastructure/providers/model_catalog.py)。

已有统一 Chat Completions adapter、模型发现、连接探测和配置解析。本轮修复了协议结束与 Ollama 接线，也证明“列表里有 provider”不等于其实际可运行。

P0 先处理原模型 alias 的可用性；P1 建立 provider 契约矩阵：文本、工具、结构化输出、usage、拒绝、限流、流中断、取消、空 key。多数 provider 名称共享兼容适配层，不应写成已经实现多家原生协议和完整多模型兼容。

### 6.3 超时、重试与总预算

证据：[retry.py](D:/all-python/nervure/services/model/retry.py)、[deadline.py](D:/all-python/nervure/services/model/deadline.py)、[live_modules.py](D:/all-python/nervure/evals/live_modules.py)。

已有基于错误分类的重试和 whole-call deadline，本轮堵住 NaN/Inf 配置。实际 retry 是非缓冲实时转发，不能用旧文档的“缓冲后再提交”解释当前行为。

P1 把主模型、selector、压缩、memory child、普通 child、重试纳入同一生产用量账本，区分 reservation/reported/unknown，明确定价和取消边界。当前 live harness 有受控账本，但不是 runtime 全局预算能力；mini 的限制机制可作设计参照而非严格费用上界保证。[S4]

### 6.4 会话与持久化恢复

证据：[message_store.py](D:/all-python/nervure/services/context/message_store.py)、[transcript.py](D:/all-python/nervure/services/context/transcript.py)、[recovery.py](D:/all-python/nervure/services/context/recovery.py)、[session_state.py](D:/all-python/nervure/services/context/session_state.py)。

消息链、外置结果、恢复状态和 CLI resume 已形成实际机制。本轮修复同进程切换会话时的落盘竞态，并验证打开失败可重试。

P1 补进程被强制终止、JSONL 最后一行损坏、状态文件落后于 transcript、外置结果缺失、重复恢复等故障矩阵。当前不是数据库事务日志，不能宣称进程崩溃或断电后绝不丢数据。

### 6.5 动态工具结果预算

证据：[CompactionConfig](D:/all-python/nervure/services/compaction/types.py)、[动态预算回归](D:/all-python/nervure/tests/test_tool_result_dynamic_budget.py)。

已有按当前占用动态分配单结果预算的机制：`min(hard_cap, remaining * ratio)`，默认 hard cap 16,000、ratio 0.25；remaining 相对压缩阈值计算，并随前面已投影消息递减。大结果可以外置，原消息不因投影直接改写，trace 有决策记录。这比固定字符阈值更有可解释性。

不足是阈值仍为启发式，前缀预览未必包含 grep/test 输出中真正关键部分，反复外置和重读也可能增加成本。P1 用同一批大日志任务比较固定阈值、当前动态预算、工具定制摘要，记录成功率、重读次数、上下文超限和总调用成本。**本轮没有完成这个 A/B，不能写成“动态预算已提升任务效果”。**

### 6.6 全文压缩与长期任务信息保留

证据：[service.py](D:/all-python/nervure/services/compaction/service.py)、[token_estimator.py](D:/all-python/nervure/services/compaction/token_estimator.py)、[压缩测试](D:/all-python/nervure/tests/test_compaction_service.py)。

有 micro/auto/manual/reactive 管线、tool 配对安全和摘要恢复。本轮 B6 是实测发现并验证的代表性缺陷。

P1 建立带事实锚点的长任务回放集，衡量文件名、失败原因、未完成事项、约束和工具引用的保留情况，同时测最终任务完成而非只测摘要长度。估算器不等于真实 tokenizer；本轮估算 40,628 与真实单请求 input 数值不同，不应混用。保留 anchor 也可能本身很大，修复不是保证任何输入都必然缩小。

### 6.7 长期记忆提取与生命周期

证据：[extraction.py](D:/all-python/nervure/services/memory/extraction.py)、[CliRuntime](D:/all-python/nervure/ui/cli/types.py)、[生命周期回归](D:/all-python/nervure/tests/test_ltm_lifecycle_regressions.py)。

这是当前最值得深挖的模块：turn 结束标脏，idle/full_compact/session close 或 switch/explicit 等事件触发 consolidation，用 watermark 只处理新增消息，通过受限子运行时写入。前序 `1e01e53` 引入 deferred 改造，`5ae6213` 修复相关生命周期；本轮不能把它们重复算成新实现。

本轮真实验证了显式提取、规则落盘和 watermark 推进；未逐个真实验证所有触发器。P1 补提取失败不前移、重复触发合并、关闭等待、跨会话隔离，以及“新事实纠正旧事实”“错误记忆删除”任务。事件驱动预计减少不必要提取，但没有配对用量实验前不能宣称节省比例。

### 6.8 记忆检索与指令加载

证据：[selector.py](D:/all-python/nervure/services/memory/selector.py)、[instruction_loader.py](D:/all-python/nervure/services/memory/instruction_loader.py)、[auto_store.py](D:/all-python/nervure/services/memory/auto_store.py)。

已有 Markdown/frontmatter 存储、规则加载和相关记忆选择，便于检查与调试。本轮两个候选中选出测试规则，仅是正向 smoke。

P1 构造含无关、冲突、过期和恶意指令的记忆集，分别测选择精确率/召回率、错误注入率和删除后的不再使用。文件来自什么会话、何时更新、是否用户确认应有可追踪依据。单个 selector 成功不是“RAG 检索准确率 100%”。

### 6.9 Prompt 组装与缓存

证据：[assembler.py](D:/all-python/nervure/prompts/assembler.py)、[cache.py](D:/all-python/nervure/prompts/cache.py)、[sections.py](D:/all-python/nervure/prompts/sections.py)。

按 section 组装和缓存能降低重复构建，适合稳定系统前缀。P2 处理缓存淘汰、版本和失效条件，并按 section 记录占用。缓存命中仅表示复用，不代表模型质量提升；应避免把不断增长的规则、技能摘要和记忆都默认塞进固定前缀。

### 6.10 工具注册、Schema 与执行

证据：[registry.py](D:/all-python/nervure/services/tools/registry.py)、[executor.py](D:/all-python/nervure/services/tools/executor.py)、[conflicts.py](D:/all-python/nervure/services/tools/conflicts.py)、[file_state.py](D:/all-python/nervure/services/tools/file_state.py)。

descriptor、schema、权限分类、冲突控制和结果投影分层，已有相当多回归。真实 read/edit/read 闭环也已跑通。

P1 强化多个工具同轮执行时的依赖、部分失败和取消语义，统一 error code、可重试性、结果来源。schema 合法不等于语义可执行，也不保证生成路径在执行时仍指向同一对象。延迟工具发现要验证是否真的减少输入成本、是否漏掉需要的工具。

### 6.11 文件编辑与文本搜索

证据：[read_file](D:/all-python/nervure/tools/read_file/tool.py)、[edit_file](D:/all-python/nervure/tools/edit_file/tool.py)、[write_file](D:/all-python/nervure/tools/write_file/tool.py)、[grep](D:/all-python/nervure/tools/grep/tool.py)、[glob](D:/all-python/nervure/tools/glob/tool.py)。

已有读写、替换、文件状态检查和搜索；但多个路径仍存在先全读/全收集再截断，已记录为 TD-020。P1 测大文件、编码/换行、二进制、外部并发改动和匹配爆量；输出限制应尽可能在 I/O 和子进程读取阶段生效，而不只是最终展示截断。

编辑器是否好用要看失败恢复和验证反馈。可以借鉴 Aider/Cline 的验证工作流，但不应把新编辑格式直接等同于更高修复率。[S1][S6]

### 6.12 代码理解：repo_map 与 symbol_search

证据：[repo_map](D:/all-python/nervure/tools/repo_map/tool.py)、[symbol_search](D:/all-python/nervure/tools/symbol_search/tool.py)、[v1 evidence](D:/all-python/nervure/docs/evidence/v1-evidence.md)。

当前主要是 Python AST 符号抽取与定位，有文件扫描上限，缺跨语言引用图、增量索引与精确引用关系；不要把它称为 Aider 式 PageRank 仓库图。[S1]

P1 先建“任务描述到目标文件/符号”标注集，比较 grep、AST、再考虑图排序。历史 repo_map 小样本采用和 symbol exact probe 记录不能证明普遍收益；有证据后再做跨语言支持，避免先堆检索层。

### 6.13 Bash 与执行隔离

证据：[runner.py](D:/all-python/nervure/tools/bash/runner.py)、[parser.py](D:/all-python/nervure/tools/bash/parser.py)、[semantics.py](D:/all-python/nervure/tools/bash/semantics.py)、[boundary.py](D:/all-python/nervure/services/guard/boundary.py)。

已有命令解析、语义分类、工作目录和路径 guard。**它们不是 OS sandbox**：解释器可访问网络、环境和进程，路径检查不能覆盖所有运行时副作用。headless 的全批准模式必须依赖外部容器边界。

P0 在无人值守评测前，建立可替换的执行环境接口，先实现容器中受限挂载、环境变量白名单、超时和完整进程树回收。OpenHands 的 workspace/environment 分层和 mini 的环境抽象可参考，但本轮不把这些新增到产品。[S2][S4]

### 6.14 Guard、权限与项目配置

证据：[guard policy](D:/all-python/nervure/services/guard/policy.py)、[permission policy](D:/all-python/nervure/services/permissions/policy.py)、[project settings](D:/all-python/nervure/services/permissions/project_settings.py)。

已有 deny 优先、多层规则、session grant 与作用域控制，不能被 hook 或工具返回的文字自行覆盖。P0 用规则组合测试固定这些语义；P1 增加路径链接变化、外部目录、环境敏感文件及跨工具授权场景。

信任仓库的配置、批准一次工具、授予整个会话权限是不同动作。OpenCode 的模式最后匹配生效只能作对照，不能原样搬进本项目的 deny-first 规则。[S7]

### 6.15 MCP

证据：[manager.py](D:/all-python/nervure/services/mcp/manager.py)、[trust.py](D:/all-python/nervure/services/mcp/trust.py)、[MCP 测试](D:/all-python/nervure/tests/test_mcp_manager.py)。

已支持配置、信任、工具发现和 stdio/SSE/streamable HTTP，原测试会启动本地测试服务，不是纯 mock；本轮 B5 限制了可能重复写的自动 retry。

P0/P1 风险是 `connect_all_blocking()` 用 `asyncio.run()` 或短命线程 loop 建立长期连接，后续 CLI 使用与关闭可能跨 loop；本轮未以完整 CLI composition 复现，原同 loop 服务测试不能排除它。下一步应以真实本地 stdio/HTTP 服务覆盖 build、连续调用、重连、close，并测试请求已执行但响应丢失。工具 annotations 是服务端声明，不是可信执行证明。

### 6.16 Subagent 与并行协作

证据：[runner.py](D:/all-python/nervure/services/subagents/runner.py)、[profiles.py](D:/all-python/nervure/services/subagents/profiles.py)、[forking.py](D:/all-python/nervure/services/subagents/forking.py)。

已有 child runtime、工具 profile、上下文 fork 和状态隔离，本轮 Explore 读文件和 memory child 写记忆都真实运行过。

P1 测主任务取消后的 child 回收、共享 workspace 写冲突、子任务失败合并、总预算归属。先比较单 agent 与受限 Explore 的配对任务，再判断是否值得扩大并行；“有子 agent”不等于多 agent 必然更快、更便宜或更准。

### 6.17 Skills 与 Hooks

证据：[skill loader](D:/all-python/nervure/services/skills/loader.py)、[skill catalog](D:/all-python/nervure/services/skills/catalog.py)、[hook registry](D:/all-python/nervure/services/hooks/registry.py)。

已有技能发现、inline/fork 执行和生命周期 hook；权限作用域已有回归，是扩展业务流程的入口。

P1 关注 hook 超时/异常/重入的隔离和权限优先级；P2 做技能版本、冲突、可追溯来源和输入范围约束。模型读到的技能文本不能成为突破 guard 的授权。验证应包含不可信仓库内技能，而不仅是正常技能加载。

### 6.18 Tasks 与后台任务

证据：[task store](D:/all-python/nervure/services/tasks/store.py)、[background manager](D:/all-python/nervure/services/background_tasks/manager.py)、[notifications](D:/all-python/nervure/services/background_tasks/notifications.py)。

已有任务状态、依赖和后台进程/agent 结果通知，本轮补了 task 路径校验。TaskStore 锁仅作用于实例，不提供多实例/多进程共享文件的事务保证。

P1 补重复领取、依赖闭环、崩溃恢复、过期 worker 和跨 session 通知验证。后台任务状态主要在进程内，停止只直接 terminate/kill 持有进程，不能宣称完整进程树终止或重启续跑。没有这层证据前，项目不宜包装为可靠的持久任务调度平台。

### 6.19 Plan、提问与人工介入

证据：[plan transitions](D:/all-python/nervure/services/plans/transitions.py)、[plan store](D:/all-python/nervure/services/plans/store.py)、[batch.py](D:/all-python/nervure/ui/cli/batch.py)、[questions](D:/all-python/nervure/services/questions/prompter.py)。

已有 Plan 模式、计划持久化、批准与提问链路，本轮修复计划覆盖写失败。P1 覆盖批准后文件已变化、用户取消、恢复时待批准状态、重复 transition 等情况。

batch 自动选择第一选项属于现有显式设计，本轮未改。演示中应说明它不是用户真实决策，涉及需求取舍的评测不能把自动首选项当人工确认。

### 6.20 Checkpoint 与回滚

证据：[checkpoint store](D:/all-python/nervure/services/checkpoints/store.py)、[CLI checkpoint tests](D:/all-python/nervure/tests/test_cli_checkpoint_state.py)。

已有文件快照及恢复入口，但不是完整 Git worktree 或多文件事务；bash、依赖安装、网络写入等副作用不一定可回滚，多文件恢复也可能部分完成。

P1 明确“可恢复什么”，加入新增/删除/重命名/外部修改/恢复中断矩阵。把 diff 审阅与验证结果呈现完整后，再考虑 worktree 管理；不要用“支持 checkpoint”推导所有操作可撤销。

### 6.21 附件与目录上下文

证据：[collector.py](D:/all-python/nervure/services/attachments/collector.py)、[resolver.py](D:/all-python/nervure/services/attachments/resolver.py)、[projector.py](D:/all-python/nervure/services/attachments/projector.py)。

解析、忽略、收集和投影分层已具备，可向模型补充文件与目录信息。P1 关注超大目录/文件的前置限额、重复注入、二进制和编码、链接及权限一致性；TD-020 已记录部分全量收集成本。附件是外部内容，不应隐式提升为系统级指令。

### 6.22 CLI 与 Runtime API 边界

证据：[app.py](D:/all-python/nervure/ui/cli/app.py)、[types.py](D:/all-python/nervure/ui/cli/types.py)、[terminal](D:/all-python/nervure/ui/cli/terminal/persistent_app.py)。

CLI 有流式渲染、resume、权限、计划、任务及连接页面，已不只是演示脚本。UI 约占本次统计运行时代码的三分之一，继续扩展多端会提高维护成本。

P1 把最小 `build/run/close` 生命周期整理成稳定 Python API 和示例，CLI/headless 共用契约；P2 补真实 TTY、小窗口、中文、Windows/Linux、取消与恢复组合测试。可以借鉴 OpenHands/Cline 的引擎与界面边界，但不必为实习项目新增 IDE 扩展。[S2][S6]

### 6.23 可观测性

证据：[trace.py](D:/all-python/nervure/services/observability/trace.py)、[sanitize.py](D:/all-python/nervure/services/observability/sanitize.py)、[error_log.py](D:/all-python/nervure/services/observability/error_log.py)。

trace/error 和压缩/记忆决策事件是现有优势，本轮修复了 sink 落盘竞态。P1 统一 session/run/attempt/child 关联、版本化 schema、脱敏以及 dropped_count 展示，并把成本和失败归因连起来。

best-effort 日志不等于审计事务日志。默认只保留诊断所需元数据，原始 prompt、工具内容和路径应有明确保留策略；本轮提交的是白名单聚合数据。

### 6.24 评测、CI、打包与文档

证据：[evals README](D:/all-python/nervure/evals/README.md)、[baseline](D:/all-python/nervure/evals/baselines/nervure-v0-baseline.json)、[import tests](D:/all-python/nervure/tests/test_import_boundaries.py)、[pyproject.toml](D:/all-python/nervure/pyproject.toml)、[architecture.md](D:/all-python/nervure/architecture.md)。

已有任务数据、Harbor 接口、ATIF 转换、指标汇总和历史 baseline。本轮 headless 生命周期与退出状态得到修复，数据集静态校验通过。

主要不足是没有 `.github` CI，安装/构建/跨平台矩阵缺证据，没有本轮 coverage/lint/typing 结果；import-boundary 测试主要是四组源码字符串断言，不是完整依赖图验证。架构文档仍有“缓冲式 retry”等与当前实现不一致的表述，旧路径和 session memory 描述也应校准。

P0/P1 先做稳定离线 CI、干净安装、文档对齐和可复现任务运行。Harbor 可选依赖要求 Python >=3.12，当前测试环境为 3.11，不应把离线 pytest 当作 Harbor 已验证。

## 7. 历史成绩怎么使用

2026-08-17 的 `nervure-v0-baseline.json` 记录 12 个 medium 任务、9 个通过，即历史 75%；来源是旧 revision `f0e9cec...`，且 `dirty=true`、`atif_all_valid=false`。原始任务目录指向历史评测磁盘，本轮没有复核这些原始运行。

因此可以说“仓库保存了一次 12 题历史 baseline”，不能说“当前版本通过率 75%”，更不能与其他项目采用不同模型、数据集和预算的公开分数排榜。旧 evidence 中的高缓存命中、单次压缩和少量代码定位探针，也不应外推为普遍性能提升。

下一次实验应固定：代码 revision、模型 ID 和参数、数据集 hash、环境镜像、输入预算、输出预算、总时限、工具权限、随机性设置、独立工作区、成功判据、原始轨迹以及基础设施错误分类。训练/开发/最终评测任务分开，已经用于调 prompt 的题不能当未见测试集。

## 8. 建设路线：先补证据，再扩展能力

这是一份按工作量估计的路线，不是承诺到期必然完成，也不会自动继续消耗本轮 API 预算。

| 时间档 | 任务 | 应交付的产物 | 验收标准 | 面试价值 |
| --- | --- | --- | --- | --- |
| 半天 | 校准 README/architecture；列出来源与个人改造；处理模型 alias 和运行说明 | 贡献说明、准确架构图、可用配置示例、三条本轮 bug 案例 | 文档能对应源码和提交；不含 key；新人能理解最小运行链路 | 讲得清项目归属、边界和真实工作 |
| 1 天 | 建立离线 CI 与最小可复现演示，打通干净环境安装 | Windows/Linux 测试任务、安装验证、固定依赖证据、演示脚本 | 无真实 key 也能跑离线套件；失败有正确 exit code；合成样例输出可机器断言 | 工程交付、调试和可维护性 |
| 3 天 | 建立第一批固定任务与故障矩阵，优先预算、记忆、压缩三个主题 | 建议先 12-20 题、每题独立 workspace、轨迹/用量聚合、失败归因 | 同模型与限额可重跑；记录所有失败；关键任务有隐藏验收；MCP/持久化故障有回归 | 能解释实验设计，而不只是展示功能 |
| 1 周 | 完成一个明确主题的配对消融，并补必要隔离 | 固定阈值 vs 动态预算，或每轮提取 vs deferred；mini 基线；容器运行说明 | 同任务同模型同限制，建议至少 3 次重复；给出成功/成本/耗时分布及不确定性，不只均值 | 把工程改造与任务效果建立可质询的因果证据 |

一周内应择一个主问题做深，其他模块先有清晰边界和失败测试。推荐主问题为：**动态工具结果预算与 deferred memory 在长 coding 任务中的效果及可靠性**。若样本不足，应如实报告探索性结果，不用小样本造显著结论。

建议实验最小设计：

1. 代码定位任务：grep 基线、当前 AST、可选引用图，测目标定位、无效读取和最终修复。
2. 大输出任务：固定截断与动态预算，测成功、额外重读、context-limit 错误和全链路 tokens。
3. 跨会话任务：无记忆、现有记忆、deferred 改造，加入过期与矛盾事实，测帮助和污染。
4. 稳定性任务：流中断、timeout、磁盘失败、已执行但丢响应、取消 child，测状态与副作用。
5. 不在一开始混合所有改造；每次只比较一个因素，避免无法解释收益来自模型、prompt 还是模块。

## 9. 实习证据与表达边界

Git 历史压缩或作者字段变更不会改变代码来源。仓库 `LICENSE` 保留原始版权信息，本轮未修改。贡献需要分清上游基础、后续个人改造和本轮 AI 辅助修复；不能仅靠新的初始 commit 证明全量原创。

| 表述 | 当前是否支持 | 需要的证据 / 更稳妥表达 |
| --- | --- | --- |
| “基于既有 Python agent runtime 改造记忆和工具预算” | 可以写，但应能解释本人承担部分 | 对应提交、设计理由、回归和失败案例 |
| “补充真实模型模块验证与受限预算测试” | 可以写，注明 AI 协助和合成 smoke 范围 | 本报告、聚合 JSON、harness、895 项离线结果 |
| “定位并修复长单轮压缩回退问题” | 可以写成具体案例 | 原失败、确定性回归、相同 fixture 的真实复测 |
| “节约 X% 成本 / 提高 X% 修复率” | 当前不能写 | 缺同条件配对任务和全链路用量证据 |
| “多模型全面兼容 / 可靠沙箱 / 生产级调度” | 当前不能写 | 只有一类真实 provider smoke，缺对应验证矩阵与实现 |
| “从零独立原创全部系统 / 训练了 coding 模型” | 当前不能写 | 与来源及本轮工作不符 |

可用的项目简介示例：**“基于现有 Python coding-agent runtime，围绕长期任务的记忆生命周期和动态工具结果预算开展改造与验证；补充故障注入回归、真实模型模块 smoke 及用量记录，分析压缩和持久化边界。”** 具体哪些工作由本人完成，应按真实情况替换，不能把助手实现的全部工作自动认领为独立贡献。

面试应准备回答：

1. 为什么不是每轮都提取记忆？watermark 在失败和会话切换时如何保持正确？
2. 工具结果动态预算怎么算？多结果为什么不能各自占满剩余窗口？
3. 压缩为什么可能越压越长？为什么保留 user anchor 但跳过中间步骤？
4. 网络超时是否等于操作没执行？MCP 为什么不能无条件重试写操作？
5. 路径 guard 为什么不是 sandbox？checkpoint 能撤销哪些副作用？
6. 895 个测试和 7 种真实场景各自证明什么？哪些仍未证明？
7. 项目哪些来自上游、哪些是后续改造、哪些借助 AI 完成？如何现场修改并验证？

**达到“完善的实习项目”的最低建议标准**：来源清楚、安装可复现、一个完整任务演示、全量离线 CI、有限但诚实的真实任务评测、一个主模块消融、可讲透的失败案例，以及明确的隔离/成本/恢复边界。先做到这些，比继续加一批工具更有说服力。

## 10. 官方来源

来源由官方 GitHub API 读取，以下固定 SHA 对应本轮观察，不声明它们永远是最新版本。正文 [Sx] 引用官方事实，比较与建设建议属于本报告分析。网页搜索工具本轮未返回可用正文，因此不编造检索引用编号；下面提供可复核的官方源地址。

**[S1] Aider**，快照 `5dc9490bb35f9729ef2c95d00a19ccd30c26339c`：

- `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/README.md`
- `https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repomap.py`

**[S2] OpenHands Software Agent SDK**，快照 `df2ea8fa5542d5d2a543e108bc8b2d4fbbab34b1`：

- `https://github.com/OpenHands/software-agent-sdk/blob/df2ea8fa5542d5d2a543e108bc8b2d4fbbab34b1/README.md`

**[S3] OpenHands Agent Canvas**，快照 `f7fb0c4b21f5ed726edbba8a6309634ef434b004`：

- `https://github.com/OpenHands/OpenHands/blob/f7fb0c4b21f5ed726edbba8a6309634ef434b004/README.md`

**[S4] mini-swe-agent v2**，快照 `04d809ceab9df28f9adaed044884180159172930`：

- `https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/README.md`
- `https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py`

**[S5] SWE-agent**，快照 `3ea751c087f32b16e039a2233dd6eefecef325d5`：

- `https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/README.md`

**[S6] Cline**，快照 `c21b17255b228e88a1518c18a73a473ee5876362`：

- `https://github.com/cline/cline/blob/c21b17255b228e88a1518c18a73a473ee5876362/README.md`

**[S7] OpenCode**，`anomalyco/opencode` 的 dev 分支快照 `ecbc6ccac85b3e8087b6445e584318419b9e2b34`：

- `https://github.com/anomalyco/opencode/blob/ecbc6ccac85b3e8087b6445e584318419b9e2b34/README.md`
- `https://github.com/anomalyco/opencode/blob/ecbc6ccac85b3e8087b6445e584318419b9e2b34/packages/web/src/content/docs/permissions.mdx`
- `https://github.com/anomalyco/opencode/blob/ecbc6ccac85b3e8087b6445e584318419b9e2b34/packages/web/src/content/docs/lsp.mdx`

**[S8] Ollama 官方兼容 API 文档**，2026-09-07 UTC 查阅，未固定 SHA：

- `https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx`

本轮代码固定版本根地址：`https://github.com/user0452/Nervure/tree/bc43c1b7724efebcd6c9d6c77c28b8ef0a213321`。仓库目前私有；对外分享前应另行检查材料，不应把原始会话、`.env` 或整个 `outputs` 公开。
