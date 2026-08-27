from dataclasses import dataclass

@dataclass(frozen=True)
class GraphIssue:
    code: str
    detail: str

def validate_graph(graph):
    issues = []
    edge_ids = set()
    for node in graph.nodes():
        for edge in graph.outgoing(node):
            if edge.id in edge_ids:
                issues.append(GraphIssue('duplicate-id', edge.id))
            edge_ids.add(edge.id)
            if edge.source != node:
                issues.append(GraphIssue('bad-source', edge.id))
            if edge.weight < 0:
                issues.append(GraphIssue('negative-weight', edge.id))
            if edge.target not in graph.nodes():
                issues.append(GraphIssue('missing-target', edge.id))
    return issues
