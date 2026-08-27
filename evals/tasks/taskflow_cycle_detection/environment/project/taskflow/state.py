from .model import TaskState

def is_terminal(state):
    return state in {TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED}

def prerequisites_satisfied(graph, task_id):
    return all((graph.tasks[p].state is TaskState.SUCCESS for p in graph.dependencies[task_id]))
