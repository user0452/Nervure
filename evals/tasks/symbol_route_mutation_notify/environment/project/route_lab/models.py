from dataclasses import dataclass

@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    weight: float
    blocked: bool = False

@dataclass
class RouteResult:
    nodes: list[str]
    cost: float

    @property
    def edges(self):
        return list(zip(self.nodes, self.nodes[1:]))
