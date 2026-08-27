from collections import deque
from .model import TaskState
from .retry import RetryPolicy
from .events import EventLog
from .result import ExecutionSummary

class Scheduler:

    def __init__(self, graph, executor):
        self.graph = graph
        self.executor = executor
        self.retry_policy = RetryPolicy()
        self.events = EventLog()
        self.full_scan_count = 0
        self._remaining = {k: len(v) for k, v in graph.dependencies.items()}
        self._ready = deque(sorted((k for k, v in self._remaining.items() if v == 0)))

    def _block_descendants(self, task_id):
        for child in self.graph.descendants(task_id):
            if self.graph.tasks[child].state is TaskState.PENDING:
                self.graph.tasks[child].state = TaskState.BLOCKED

    def _unblock_after_success(self, task_id):
        for child in self.graph.descendants(task_id):
            task = self.graph.tasks[child]
            if task.state is TaskState.BLOCKED and all((self.graph.tasks[p].state is TaskState.SUCCESS for p in self.graph.dependencies[child])):
                task.state = TaskState.PENDING
                if child not in self._ready:
                    self._ready.append(child)

    def retry(self, task_id):
        task = self.graph.tasks[task_id]
        if task.state is not TaskState.FAILED:
            raise ValueError('task is not failed')
        task.state = TaskState.PENDING
        task.error = None
        if task_id not in self._ready:
            self._ready.appendleft(task_id)

    def run_one(self):
        while self._ready:
            task_id = self._ready.popleft()
            task = self.graph.tasks[task_id]
            if task.state is not TaskState.PENDING:
                continue
            if not all((self.graph.tasks[p].state is TaskState.SUCCESS for p in self.graph.dependencies[task_id])):
                continue
            task.state = TaskState.RUNNING
            task.attempts += 1
            self.events.add('start', task_id)
            try:
                self.executor.execute(task)
                task.state = TaskState.SUCCESS
                self.events.add('success', task_id)
                self._unblock_after_success(task_id)
                for child in self.graph.dependents[task_id]:
                    self._remaining[child] = max(0, self._remaining[child] - 1)
                    if self._remaining[child] == 0 and self.graph.tasks[child].state is TaskState.PENDING:
                        self._ready.append(child)
            except Exception as exc:
                task.state = TaskState.FAILED
                task.error = str(exc)
                self.events.add('failure', task_id, str(exc))
                self._block_descendants(task_id)
            return task_id
        return None

    def run_all(self):
        while self.run_one() is not None:
            pass
        return self.summary()

    def summary(self):
        groups = {s: [] for s in TaskState}
        for k, t in self.graph.tasks.items():
            groups[t.state].append(k)
        return ExecutionSummary(sorted(groups[TaskState.SUCCESS]), sorted(groups[TaskState.FAILED]), sorted(groups[TaskState.BLOCKED]), sorted(groups[TaskState.CANCELLED]))
