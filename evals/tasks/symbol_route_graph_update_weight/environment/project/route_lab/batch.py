from .dijkstra import single_source, build_result

class BatchRouter:

    def __init__(self, service):
        self.service = service

    def routes_from(self, source, targets, constraint=None):
        dist, prev = single_source(self.service.graph, source, self.service.metrics, constraint)
        return {target: build_result(source, target, dist, prev) for target in targets}
