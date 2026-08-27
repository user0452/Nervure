# V1 Timeout Trace Analysis

日期：2026-08-19。以下是已有 Harbor trace 的事后分析，目的在于区分“模型继续探索/调用很慢”和“loop 在已完成后错误继续”的证据；本分析不改写历史 trace。

## 汇总

| case | reward / 状态 | model_call spans | 最长 model span | 未完成尾调用 | 首次明确测试成功 | 成功后继续的工具结果 |
|---|---|---:|---:|---:|---|---|
| route mutation notify / A-none | reward=1，Harbor timeout | 30 | 209388.606 ms | 1 | `08:37:10.663937Z`，`unittest discover` exit 0 | 15 model spans；15 个结果，约 7 read/glob/grep、6 bash、2 edit |
| taskflow retry state / B-repo-map | reward=1，Harbor timeout | 12 | 420938.378 ms（有 tool-call stop 的长 span）；未完成尾调用 88769.634 ms | 1 | `01:59:32.492874Z`，`unittest tests.test_basic` exit 0 | 4 model spans；3 个结果，约 1 read/glob/grep、1 edit |
| taskflow retry state / B-repo-map retry | reward=1，Harbor timeout | 25 | 399866.106 ms（有 tool-call stop 的长 span）；未完成尾调用 19264.972 ms | 1 | `02:21:31.444448Z`，`pytest tests/` exit 0 | 9 model spans；8 个结果，约 4 read/glob/grep、4 bash |
| preflight / A-none | reward=0，探索/超时 | 17 | 375781.056 ms | 1（144137.559 ms） | 无可归因的完成测试 | 不是成功后样本，主要是探索阶段慢调用 |

“工具结果”按 trace 中唯一 `tool_call_id` 统计；原始 span 数与 tool result lifecycle 不等价。route 案例的 raw trace 记录总计 51 个 tool results，但成功测试之后的 15 个是本段关心的后续调用。

## 编辑与测试时间线

- route 案例先经历一次 `pytest` 缺少依赖的失败，随后 `python -m unittest discover tests -v` 成功；成功点之后仍继续重复读取、缓存/pyc 探索和编辑，最后进入没有 `stop_reason` 的 model span。
- taskflow 普通案例在 `pytest` 不可用后使用 `unittest` 成功；随后出现再次读取/编辑并进入未完成模型调用。
- retry 案例在安装 pytest 后完整 `pytest tests/` 成功；之后仍出现多轮模型 span，最后没有完成消息。
- preflight 案例没有形成“编辑后测试通过再卡住”的闭环，不能与前三个成功后继续样本混合解释。

## 诊断

历史 trace 的最后一条 model span 没有 `stop_reason`，也没有 `message_completed`/最终 completed transition；因此没有证据证明 loop 在已收到 final answer 后错误继续。更符合：模型在测试通过后仍自行探索，加上旧 adapter 只有 HTTP transport/read timeout，没有整次 streaming model-call hard deadline，导致单次慢调用占满 Harbor 上限。

本轮新增 `services/model/deadline.py` 的 provider-neutral whole-call deadline，默认 `model_call_timeout_seconds=300`，会关闭底层 async iterator，抛不可重试 `timeout_error`，并在 span/event 中记录 deadline、elapsed 和 partial text/tool 计数。它只限制慢调用生命周期，不判断语义完成；模型仍可能在截止前继续探索。历史 trace 不因本轮实现而被重新标注。

来源：

- `C:\Users\刘\Desktop\Nervure-Traces\repo-understanding-sanity-20260819\symbol-search\A-none\symbol-search-A-none-20260819\symbol_route_mutation_notify__PoPEKSy\agent\nervure\trace.jsonl`
- `C:\Users\刘\Desktop\Nervure-Traces\repo-understanding-factorial-20260819-081535-4c\repeat-2\B-repo-map\factorial-r2-B-repo-map\taskflow_retry_state__SzoEoc4\agent\nervure\trace.jsonl`
- `C:\Users\刘\Desktop\Nervure-Traces\repo-understanding-factorial-20260819-081535-4c\repeat-2\B-repo-map\retry-taskflow-1\factorial-r2-B-repo-map-retry-taskflow-1\taskflow_retry_state__PBgtweB\agent\nervure\trace.jsonl`
- `C:\Users\刘\Desktop\Nervure-Traces\repo-understanding-factorial-preflight-20260819-075652-r2\A-none\preflight-r2-A-none\taskflow_retry_state__7ARcFFr\agent\nervure\trace.jsonl`
