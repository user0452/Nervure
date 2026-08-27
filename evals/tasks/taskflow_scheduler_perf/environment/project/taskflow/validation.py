from dataclasses import dataclass

@dataclass(frozen=True)
class ValidationIssue:
    code: str
    task_id: str
    detail: str

def validate_graph(graph):
    issues = []
    for task_id, deps in graph.dependencies.items():
        if task_id not in graph.tasks:
            issues.append(ValidationIssue('missing-task', task_id, 'dependency map references missing task'))
        for dep in deps:
            if dep not in graph.tasks:
                issues.append(ValidationIssue('missing-prerequisite', task_id, dep))
            if task_id not in graph.dependents.get(dep, set()):
                issues.append(ValidationIssue('reverse-index', task_id, dep))
    try:
        graph.topological()
    except ValueError:
        issues.append(ValidationIssue('cycle', '*', 'graph is cyclic'))
    return issues
