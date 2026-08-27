from dataclasses import dataclass

@dataclass
class ExecutionSummary:
    succeeded: list[str]
    failed: list[str]
    blocked: list[str]
    cancelled: list[str]
