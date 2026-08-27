from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance(self, other):
        return math.hypot(self.x - other.x, self.y - other.y)

class CoordinateIndex:

    def __init__(self):
        self._points = {}

    def set(self, node, x, y):
        self._points[node] = Point(float(x), float(y))

    def get(self, node):
        return self._points[node]

    def heuristic(self, node, goal):
        if node not in self._points or goal not in self._points:
            return 0.0
        return self._points[node].distance(self._points[goal])
