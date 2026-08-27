from .metrics import SearchMetrics
from .cache import RouteCache
from .dijkstra import shortest_path as dijkstra
from .astar import shortest_path as astar
from .heuristics import ZeroHeuristic

class RouteService:

    def __init__(self, graph):
        self.graph = graph
        self.metrics = SearchMetrics()
        self.cache = RouteCache()
        graph.subscribe(self.cache.invalidate_edge)

    def _edge_ids_for(self, result):
        if result is None:
            return set()
        ids = set()
        for a, b in result.edges:
            for e in self.graph.outgoing(a):
                if e.target == b:
                    ids.add(e.id)
                    break
        return ids

    def route(self, source, target, algorithm='dijkstra', heuristic=None, constraint=None, use_cache=True):
        key = (source, target, algorithm, repr(constraint))
        if use_cache:
            entry = self.cache.get(key)
            if entry:
                return entry.result
        if algorithm == 'astar':
            result = astar(self.graph, source, target, heuristic or ZeroHeuristic(), self.metrics, constraint)
        else:
            result = dijkstra(self.graph, source, target, self.metrics, constraint)
        if use_cache:
            self.cache.put(key, result, self._edge_ids_for(result))
        return result
