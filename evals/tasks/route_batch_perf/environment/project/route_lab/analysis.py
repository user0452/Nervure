from collections import defaultdict, deque

def degree_summary(graph):
    indegree = defaultdict(int)
    outdegree = {n: len(graph.outgoing(n)) for n in graph.nodes()}
    for node in graph.nodes():
        for edge in graph.outgoing(node):
            indegree[edge.target] += 1
    return {n: {'in': indegree[n], 'out': outdegree[n]} for n in graph.nodes()}

def reachable(graph, source):
    seen = set()
    q = deque([source])
    while q:
        node = q.popleft()
        if node in seen:
            continue
        seen.add(node)
        for edge in graph.outgoing(node):
            if not edge.blocked and edge.target not in seen:
                q.append(edge.target)
    return seen

def strongly_connected_hint(graph):
    nodes = graph.nodes()
    if not nodes:
        return True
    first = next(iter(nodes))
    return len(reachable(graph, first)) == len(nodes)
