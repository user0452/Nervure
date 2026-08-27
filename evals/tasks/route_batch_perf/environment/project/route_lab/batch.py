class BatchRouter:

    def __init__(self, service):
        self.service = service

    def routes_from(self, source, targets, constraint=None):
        return {target: self.service.route(source, target, constraint=constraint, use_cache=False) for target in targets}
