class RouteConstraint:

    def allows(self, edge):
        return not edge.blocked

class AvoidNodes(RouteConstraint):

    def __init__(self, nodes):
        self.nodes = set(nodes)

    def allows(self, edge):
        return not edge.blocked and edge.target not in self.nodes

class MaxEdgeWeight(RouteConstraint):

    def __init__(self, maximum):
        self.maximum = maximum

    def allows(self, edge):
        return not edge.blocked and edge.weight <= self.maximum
