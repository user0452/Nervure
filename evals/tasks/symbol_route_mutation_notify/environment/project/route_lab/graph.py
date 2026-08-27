from .models import Edge

class Graph:

    def __init__(self):
        self._nodes = set()
        self._edges = {}
        self._out = {}
        self._listeners = []
        self.version = 0

    def add_node(self, node):
        self._nodes.add(node)
        self._out.setdefault(node, [])

    def add_edge(self, source, target, weight, edge_id=None):
        if weight < 0:
            raise ValueError('negative weight')
        self.add_node(source)
        self.add_node(target)
        edge_id = edge_id or f'{source}->{target}'
        if edge_id in self._edges:
            raise ValueError('duplicate edge')
        e = Edge(edge_id, source, target, float(weight), False)
        self._edges[edge_id] = e
        self._out[source].append(edge_id)
        self._changed(edge_id)
        return edge_id

    def edge(self, edge_id):
        return self._edges[edge_id]

    def outgoing(self, node):
        return [self._edges[eid] for eid in self._out.get(node, ())]

    def nodes(self):
        return set(self._nodes)

    def subscribe(self, fn):
        self._listeners.append(fn)

    def _changed(self, edge_id):
        self.version += 1
        for fn in list(self._listeners):
            fn(edge_id)

    def update_weight(self, edge_id, weight):
        e = self._edges[edge_id]
        self._edges[edge_id] = Edge(e.id, e.source, e.target, float(weight), e.blocked)

    def set_blocked(self, edge_id, blocked=True):
        e = self._edges[edge_id]
        self._edges[edge_id] = Edge(e.id, e.source, e.target, e.weight, bool(blocked))
        self._changed(edge_id)
