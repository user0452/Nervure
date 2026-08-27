from dataclasses import dataclass

@dataclass
class CacheEntry:
    result: object
    edge_ids: set

class RouteCache:

    def __init__(self):
        self._entries = {}

    def get(self, key):
        return self._entries.get(key)

    def put(self, key, result, edge_ids):
        self._entries[key] = CacheEntry(result, set(edge_ids))

    def invalidate_edge(self, edge_id):
        stale = [k for k, v in self._entries.items() if edge_id in v.edge_ids]
        for k in stale:
            self._entries.pop(k, None)

    def __len__(self):
        return len(self._entries)
