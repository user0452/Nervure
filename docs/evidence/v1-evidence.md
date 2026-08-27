# Nervure V1 Evidence Package

日期：2026-08-19。本文只引用已落盘、可复核的 trace/report/metrics；没有把小样本方向性结果包装成因果结论。

## Full Compact 与真实缓存

唯一成功的 live provider 验证使用 `custom / mimo-v2.5-pro-1m`：

- input `14396`；cache-read `14336`；uncached `60`；output `404`；reasoning `0`；stop `stop`；duration `13688.006 ms`。
- cache-read ratio `0.9958321756043346`（约 99.583%）。
- 父请求的 system prompt、tool schema、180 条 parent message 与 compact 请求前缀一致；compact instruction 作为第 181 条消息追加。
- 成功尝试实际为 2 次 provider call（parent + compact）；同一 harness 的前两次测试预算过小而被截断，原始 trace 保留，未伪造 usage。

来源：`C:\Users\刘\Desktop\Nervure-Traces\full-compact-cache-validation-20260819\cache-validation.json`，以及同目录 `trace\full-compact-cache-validation\trace.jsonl`。

## Repo Understanding

### repo_map sanity

四个有效 trial 都 reward=1。A-none 的 `read_file=123`，B-repo-map 的 `read_file=74`；但 repo_map 自发采用率只有 `1/4`，因此只记录为方向性信号，不声称稳定收益或因果减负。对应 `glob/grep` 分别为 A `27/16`、B `35/19`。

### symbol_search exact probe

四个有效 C-symbol-search trial 均 reward=1，采用率 `4/4`，首次调用均在 turn 1，未见 correctness regression；按唯一 `tool_call_id` 计数为 6 次 symbol_search。该结果证明 focused definition lookup 在精确题型上可被模型使用，不等于已经证明大样本性能优势。

来源：`C:\Users\刘\Desktop\Nervure-Traces\repo-understanding-sanity-20260819\final-report.md`。旧 trace 的工具统计已改为唯一 call id 分析，不能把 preflight/execution/result lifecycle 重复相加。

## Harbor foundation

当前 integration 固定 Harbor `v0.21.0`，commit `64afbbcb62165950301e1a6407c729aa26d844ff`。Oracle `4/4 reward=1.0`，NOP `4/4 reward=0.0`；agent image 的 ripgrep 预装验收通过，正式 trace 中 `ripgrep_not_found=0`。Separate verifier/hidden tests 的隔离沿用现有 PoC artifact 证据。

来源：`C:\Users\刘\Desktop\Nervure-Traces\repo-understanding-sanity-20260819\final-report.md` 与 `evals/README.md`。

## 证据边界

- Full Compact cache 结论来自一次成功真实 provider response，不是合成 usage。
- repo_map 样本只有 4 个，adoption 低，不能据此强制注入或宣称 token/latency 收益。
- symbol_search 只在 exact probe 上验证 adoption/correctness；性能 margin 尚未测量。
- 本轮新增的 300 秒 model-call wall-clock deadline 尚未用于重跑 Harbor benchmark；历史 timeout trace 仍反映旧行为。
- 该包不含完整 prompt、源码、memory 正文或凭证，只保留必要的聚合数值、来源和限制。
