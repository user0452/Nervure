from .model import TaskState

def cancel(graph, task_id, propagate=True):
    task = graph.tasks[task_id]
    if task.state in {TaskState.SUCCESS, TaskState.FAILED}:
        return False
    task.state = TaskState.CANCELLED
    if propagate:
        for child in graph.descendants(task_id):
            if graph.tasks[child].state is TaskState.PENDING:
                graph.tasks[child].state = TaskState.BLOCKED
    return True
