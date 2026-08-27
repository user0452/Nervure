class DeterministicExecutor:

    def __init__(self, outcomes=None):
        self.outcomes = {k: list(v) for k, v in (outcomes or {}).items()}
        self.calls = []

    def execute(self, task):
        self.calls.append(task.id)
        seq = self.outcomes.get(task.id)
        if seq:
            value = seq.pop(0)
            if isinstance(value, Exception):
                raise value
            if value is False:
                raise RuntimeError(f'{task.id} failed')
            return value
        return task.payload
