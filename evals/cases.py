"""Stable Nervure evaluation case and suite metadata.

The case registry is data-only.  Harbor remains responsible for dataset/task
execution; this layer supplies stable IDs, selection and report metadata.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    project: str
    kind: str
    summary: str
    verifier: str = "separate"
    available: bool = False


CASES: tuple[EvalCase, ...] = (
    EvalCase("xiangqi_flying_general", "mini_xiangqi", "bugfix", "将帅照面规则", available=True),
    EvalCase("xiangqi_cannon_capture", "mini_xiangqi", "bugfix", "炮吃子边界", available=True),
    EvalCase("xiangqi_movegen_perf", "mini_xiangqi", "performance", "走法生成重复扫描", available=True),
    EvalCase("vcs_rename_status", "mini_vcs", "bugfix", "rename 状态识别", available=True),
    EvalCase("vcs_staging_snapshot", "mini_vcs", "bugfix", "提交快照不可变性", available=True),
    EvalCase("vcs_diff_perf", "mini_vcs", "performance", "status/diff 重复读取", available=True),
    EvalCase("route_astar_optimality", "route_lab", "bugfix", "A* 最短路正确性", available=True),
    EvalCase("route_cache_invalidation", "route_lab", "bugfix", "路线缓存失效", available=True),
    EvalCase("route_batch_perf", "route_lab", "performance", "批量查询重复搜索", available=True),
    EvalCase("taskflow_cycle_detection", "taskflow", "bugfix", "间接环检测", available=True),
    EvalCase("taskflow_retry_state", "taskflow", "bugfix", "retry 后状态传播", available=True),
    EvalCase("taskflow_scheduler_perf", "taskflow", "performance", "调度器重复扫描", available=True),
)

SUITES: dict[str, tuple[str, ...]] = {
    "smoke": (
        "xiangqi_flying_general",
        "vcs_staging_snapshot",
        "route_cache_invalidation",
        "taskflow_retry_state",
    ),
    "bugfix": tuple(case.case_id for case in CASES if case.kind == "bugfix"),
    "performance": tuple(case.case_id for case in CASES if case.kind == "performance"),
    "full": tuple(case.case_id for case in CASES),
}


def case_by_id(case_id: str) -> EvalCase:
    for case in CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(f"Unknown Nervure eval case: {case_id}")
