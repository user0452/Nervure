from collections import Counter

class FlowMetrics:

    def __init__(self):
        self.counters = Counter()
        self.timings = {}

    def inc(self, name, value=1):
        self.counters[name] += value

    def observe(self, name, value):
        self.timings.setdefault(name, []).append(float(value))

    def summary(self):
        result = dict(self.counters)
        for name, values in self.timings.items():
            if values:
                result[name] = {'count': len(values), 'avg': sum(values) / len(values), 'max': max(values)}
        return result

def summarize_events(event_log):
    counts = Counter((e.kind for e in event_log.events))
    by_task = {}
    for event in event_log.events:
        by_task.setdefault(event.task_id, []).append(event.kind)
    return {'counts': dict(counts), 'tasks': by_task}
