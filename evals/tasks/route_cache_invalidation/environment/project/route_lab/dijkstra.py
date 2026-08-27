import heapq, math
from .models import RouteResult

def shortest_path(graph, source, target, metrics, constraint=None):
    metrics.searches += 1
    dist = {source: 0.0}
    prev = {}
    pq = [(0.0, source)]
    while pq:
        d, node = heapq.heappop(pq)
        if d != dist.get(node):
            continue
        metrics.expansions += 1
        if node == target:
            break
        for edge in graph.outgoing(node):
            if edge.blocked or (constraint and (not constraint.allows(edge))):
                continue
            metrics.relaxations += 1
            nd = d + edge.weight
            if nd < dist.get(edge.target, math.inf):
                dist[edge.target] = nd
                prev[edge.target] = (node, edge.id)
                heapq.heappush(pq, (nd, edge.target))
    if target not in dist:
        return None
    nodes = [target]
    cur = target
    while cur != source:
        cur = prev[cur][0]
        nodes.append(cur)
    nodes.reverse()
    return RouteResult(nodes, dist[target])

def single_source(graph, source, metrics, constraint=None):
    metrics.searches += 1
    dist = {source: 0.0}
    prev = {}
    pq = [(0.0, source)]
    while pq:
        d, node = heapq.heappop(pq)
        if d != dist.get(node):
            continue
        metrics.expansions += 1
        for edge in graph.outgoing(node):
            if edge.blocked or (constraint and (not constraint.allows(edge))):
                continue
            metrics.relaxations += 1
            nd = d + edge.weight
            if nd < dist.get(edge.target, float('inf')):
                dist[edge.target] = nd
                prev[edge.target] = node
                heapq.heappush(pq, (nd, edge.target))
    return (dist, prev)

def build_result(source, target, dist, prev):
    if target not in dist:
        return None
    nodes = [target]
    cur = target
    while cur != source:
        cur = prev[cur]
        nodes.append(cur)
    nodes.reverse()
    return RouteResult(nodes, dist[target])
