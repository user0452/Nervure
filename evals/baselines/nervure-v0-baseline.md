# Nervure V0 baseline

- Dataset: `nervure-medium-coding 0.1.0`
- Revision: `f0e9cec8ae103049efdb57ca082960f242d58c1f` (dirty: `True`)
- Models: mimo-v2.5-pro-1m
- Providers: custom

| Case | Result | Reward | Calls | Uncached | Tools | Time (ms) | ATIF |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| xiangqi_flying_general | PASS | 1.0 | 19 | 67296 | 39 | 140830.328 | True |
| xiangqi_cannon_capture | PASS | 1.0 | 13 | 45472 | 23 | 112499.582 | True |
| xiangqi_movegen_perf | AGENT_FAILURE | 0.0 | 23 | 99200 | 49 | 598086.288 | True |
| vcs_rename_status | PASS | 1.0 | 27 | 99122 | 55 | 294498.241 | True |
| vcs_staging_snapshot | PASS | 1.0 | 20 | 77008 | 38 | 208333.265 | True |
| vcs_diff_perf | AGENT_FAILURE | 0.0 | 31 | 109112 | 71 | 899488.230 | None |
| route_astar_optimality | PASS | 1.0 | 25 | 157691 | 52 | 645026.741 | True |
| route_cache_invalidation | PASS | 1.0 | 26 | 89961 | 47 | 337813.158 | True |
| route_batch_perf | PASS | 1.0 | 19 | 67095 | 52 | 224483.170 | True |
| taskflow_cycle_detection | PASS | 1.0 | 14 | 58713 | 32 | 151444.256 | True |
| taskflow_retry_state | PASS | 1.0 | 19 | 82249 | 23 | 291652.232 | True |
| taskflow_scheduler_perf | AGENT_FAILURE | 0.0 | 19 | 69435 | 53 | 899613.847 | None |

## Summary

- Passed: 9 / 12 (75.00%)
- Infrastructure errors: 0
- Total model calls: 255
- Average model calls per task: 21.25
- Input / cache read / uncached / output / reasoning: 2600594 / 1578240 / 1022354 / 162952 / 0
- Cache hit ratio: 60.69%
- Total runtime (ms): 4803769.338
- Average runtime per task (ms): 400314.112
- Tools / errors / unknown / permission denied: 534 / 57 / 0 / 12
- Selector fresh / cache hits / parse failures: 0 / 190 / 0
- Selector input / output / reasoning tokens: 0 / 0 / 0
- LTM extraction jobs / model calls: 0 / 0 (the current metrics schema does not expose LTM-only token usage)
- Full compacts: 0
- Subagents: 0
- Changed files / diff bytes: 10 / 7632
- ATIF all valid: False
