from .graph import TaskGraph

def from_mapping(mapping):
    g = TaskGraph()
    names = set(mapping)
    for deps in mapping.values():
        names.update(deps)
    for name in sorted(names):
        g.add_task(name)
    for task, deps in mapping.items():
        for dep in deps:
            g.add_dependency(task, dep)
    return g
