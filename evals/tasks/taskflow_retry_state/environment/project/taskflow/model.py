from dataclasses import dataclass, field
from enum import Enum

class TaskState(str, Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'

@dataclass
class Task:
    id: str
    payload: object = None
    max_retries: int = 0
    state: TaskState = TaskState.PENDING
    attempts: int = 0
    error: str | None = None
