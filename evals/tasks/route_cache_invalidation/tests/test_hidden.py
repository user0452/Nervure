import sys,unittest
sys.path.insert(0,'/workspace')
from route_lab.graph import Graph
from route_lab.service import RouteService
class Hidden(unittest.TestCase):
    def test_weight_change_invalidates_only_dependent_route(self):
        g=Graph(); g.add_edge('A','B',1); g.add_edge('B','C',1); g.add_edge('A','C',5); g.add_edge('X','Y',2)
        s=RouteService(g); self.assertEqual(s.route('A','C').cost,2); self.assertEqual(s.route('X','Y').cost,2); self.assertEqual(len(s.cache),2)
        g.update_weight('B->C',10)
        self.assertEqual(s.route('A','C').cost,5)
        self.assertGreaterEqual(len(s.cache),1)
    def test_block_change_also_invalidates(self):
        g=Graph(); g.add_edge('A','B',1); g.add_edge('B','C',1); g.add_edge('A','C',4); s=RouteService(g); self.assertEqual(s.route('A','C').cost,2); g.set_blocked('B->C',True); self.assertEqual(s.route('A','C').cost,4)
if __name__=='__main__': unittest.main()
