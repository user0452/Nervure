from dataclasses import dataclass, field

@dataclass
class Worker:
    name: str
    labels: set[str] = field(default_factory=set)
    busy: bool = False

class WorkerPool:

    def __init__(self, workers):
        self.workers = list(workers)

    def acquire(self, required=()):
        required = set(required)
        for worker in self.workers:
            if not worker.busy and required <= worker.labels:
                worker.busy = True
                return worker
        return None

    def release(self, worker):
        worker.busy = False

    def available(self):
        return [w for w in self.workers if not w.busy]
