from dataclasses import dataclass
from .service import RouteService

@dataclass
class AlternativeSet:
    primary: object
    alternatives: list

def alternatives(graph, source, target, limit=3):
    service = RouteService(graph)
    primary = service.route(source, target, use_cache=False)
    if primary is None:
        return AlternativeSet(None, [])
    found = []
    for a, b in primary.edges:
        edge_id = next((e.id for e in graph.outgoing(a) if e.target == b))
        edge = graph.edge(edge_id)
        graph.set_blocked(edge_id, True)
        try:
            candidate = service.route(source, target, use_cache=False)
            if candidate and all((candidate.nodes != x.nodes for x in found)) and (candidate.nodes != primary.nodes):
                found.append(candidate)
        finally:
            graph.set_blocked(edge_id, edge.blocked)
        if len(found) >= limit:
            break
    found.sort(key=lambda r: r.cost)
    return AlternativeSet(primary, found[:limit])
