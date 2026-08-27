from dataclasses import dataclass

@dataclass
class ExecutionLayer:
    index: int
    tasks: list[str]

def layers(graph):
    remaining = {k: set(v) for k, v in graph.dependencies.items()}
    done = set()
    out = []
    index = 0
    while len(done) < len(graph.tasks):
        ready = sorted((k for k, deps in remaining.items() if k not in done and deps <= done))
        if not ready:
            raise ValueError('cycle or unsatisfied dependency')
        out.append(ExecutionLayer(index, ready))
        done.update(ready)
        index += 1
    return out

def critical_depth(graph):
    depth = {}
    for task in graph.topological():
        depth[task] = 1 + max((depth[d] for d in graph.dependencies[task]), default=0)
    return max(depth.values(), default=0)
