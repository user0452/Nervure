def to_dot(graph):
    lines = ['digraph taskflow {']
    for task_id in sorted(graph.tasks):
        state = graph.tasks[task_id].state.value
        lines.append(f'  "{task_id}" [label="{task_id}\\n{state}"];')
    for task_id, deps in sorted(graph.dependencies.items()):
        for dep in sorted(deps):
            lines.append(f'  "{dep}" -> "{task_id}";')
    lines.append('}')
    return '\n'.join(lines)
