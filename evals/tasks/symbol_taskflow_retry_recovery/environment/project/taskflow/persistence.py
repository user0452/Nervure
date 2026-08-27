import json
from .model import Task, TaskState
from .graph import TaskGraph

def dumps(graph):
    return json.dumps({'tasks': [{'id': t.id, 'payload': t.payload, 'max_retries': t.max_retries, 'state': t.state.value, 'attempts': t.attempts} for t in graph.tasks.values()], 'deps': {k: sorted(v) for k, v in graph.dependencies.items()}}, sort_keys=True)

def loads(text):
    raw = json.loads(text)
    g = TaskGraph()
    for row in raw['tasks']:
        t = Task(row['id'], row.get('payload'), row.get('max_retries', 0), TaskState(row.get('state', 'pending')), row.get('attempts', 0))
        g.add_task(t)
    for task, deps in raw['deps'].items():
        for dep in deps:
            g.dependencies[task].add(dep)
            g.dependents[dep].add(task)
    return g
