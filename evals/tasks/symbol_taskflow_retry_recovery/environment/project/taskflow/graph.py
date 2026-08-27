from .model import Task

class TaskGraph:

    def __init__(self):
        self.tasks = {}
        self.dependencies = {}
        self.dependents = {}

    def add_task(self, task):
        if isinstance(task, str):
            task = Task(task)
        if task.id in self.tasks:
            raise ValueError('duplicate task')
        self.tasks[task.id] = task
        self.dependencies[task.id] = set()
        self.dependents[task.id] = set()
        return task

    def add_dependency(self, task_id, prerequisite_id):
        if task_id not in self.tasks or prerequisite_id not in self.tasks:
            raise KeyError('unknown task')
        if task_id == prerequisite_id or self._reachable(prerequisite_id, task_id):
            raise ValueError('dependency cycle')
        self.dependencies[task_id].add(prerequisite_id)
        self.dependents[prerequisite_id].add(task_id)

    def _reachable(self, start, target):
        stack = [start]
        seen = set()
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self.dependencies[node])
        return False

    def descendants(self, task_id):
        out = set()
        stack = list(self.dependents[task_id])
        while stack:
            node = stack.pop()
            if node in out:
                continue
            out.add(node)
            stack.extend(self.dependents[node])
        return out

    def topological(self):
        indegree = {k: len(v) for k, v in self.dependencies.items()}
        ready = sorted((k for k, v in indegree.items() if v == 0))
        out = []
        while ready:
            n = ready.pop(0)
            out.append(n)
            for child in sorted(self.dependents[n]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        if len(out) != len(self.tasks):
            raise ValueError('cycle')
        return out
