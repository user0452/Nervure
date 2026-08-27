from dataclasses import dataclass
from .model import TaskState

@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    state: str
    attempts: int
    error: str | None

@dataclass(frozen=True)
class FlowSnapshot:
    tasks: tuple[TaskSnapshot, ...]

def capture(graph):
    rows = []
    for task_id in sorted(graph.tasks):
        t = graph.tasks[task_id]
        rows.append(TaskSnapshot(task_id, t.state.value, t.attempts, t.error))
    return FlowSnapshot(tuple(rows))

def restore_states(graph, snapshot):
    for row in snapshot.tasks:
        if row.task_id in graph.tasks:
            t = graph.tasks[row.task_id]
            t.state = TaskState(row.state)
            t.attempts = row.attempts
            t.error = row.error
