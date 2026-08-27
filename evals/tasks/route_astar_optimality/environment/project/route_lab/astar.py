import heapq, math
from .models import RouteResult

def shortest_path(graph, source, target, heuristic, metrics, constraint=None):
    metrics.searches += 1
    g = {source: 0.0}
    prev = {}
    closed = set()
    pq = [(heuristic(source, target), 0.0, source)]
    while pq:
        _, cost, node = heapq.heappop(pq)
        if cost != g.get(node):
            continue
        if node in closed:
            continue
        closed.add(node)
        metrics.expansions += 1
        if node == target:
            break
        for edge in graph.outgoing(node):
            if edge.blocked or (constraint and (not constraint.allows(edge))):
                continue
            metrics.relaxations += 1
            tentative = cost + edge.weight
            if tentative < g.get(edge.target, math.inf):
                g[edge.target] = tentative
                prev[edge.target] = node
                if edge.target in closed:
                    continue
                heapq.heappush(pq, (tentative + heuristic(edge.target, target), tentative, edge.target))
    if target not in g:
        return None
    nodes = [target]
    cur = target
    while cur != source:
        cur = prev[cur]
        nodes.append(cur)
    nodes.reverse()
    return RouteResult(nodes, g[target])
