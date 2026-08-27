from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass
class SavedRoute:
    name: str
    source: str
    target: str
    nodes: list[str]
    cost: float
    created_at: str

class RouteBook:

    def __init__(self):
        self._routes = {}

    def save(self, name, source, target, result):
        if result is None:
            raise ValueError('cannot save missing route')
        row = SavedRoute(name, source, target, list(result.nodes), result.cost, datetime.now(timezone.utc).isoformat())
        self._routes[name] = row
        return row

    def get(self, name):
        return self._routes[name]

    def remove(self, name):
        return self._routes.pop(name)

    def list(self):
        return [self._routes[k] for k in sorted(self._routes)]
