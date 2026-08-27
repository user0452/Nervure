import unittest
from route_lab.graph import Graph
from route_lab.service import RouteService
from route_lab.serialization import dumps, loads

class Basic(unittest.TestCase):

    def graph(self):
        g = Graph()
        g.add_edge('A', 'B', 2)
        g.add_edge('B', 'C', 3)
        g.add_edge('A', 'C', 10)
        return g

    def test_dijkstra(self):
        self.assertEqual(RouteService(self.graph()).route('A', 'C').cost, 5)

    def test_blocked(self):
        g = self.graph()
        g.set_blocked('B->C', True)
        self.assertEqual(RouteService(g).route('A', 'C').cost, 10)

    def test_roundtrip(self):
        g = self.graph()
        self.assertEqual(RouteService(loads(dumps(g))).route('A', 'C').cost, 5)
if __name__ == '__main__':
    unittest.main()
