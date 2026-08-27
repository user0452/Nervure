class ZeroHeuristic:

    def __call__(self, node, goal):
        return 0.0

class TableHeuristic:

    def __init__(self, values):
        self.values = dict(values)

    def __call__(self, node, goal):
        return float(self.values.get(node, 0.0))
