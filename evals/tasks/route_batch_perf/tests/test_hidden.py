import sys,unittest
sys.path.insert(0,'/workspace')
from route_lab.topology import grid_graph
from route_lab.service import RouteService
from route_lab.batch import BatchRouter
class Hidden(unittest.TestCase):
    def test_batch_uses_one_single_source_search(self):
        g=grid_graph(6,6); s=RouteService(g); s.metrics.reset(); out=BatchRouter(s).routes_from('0,0',['5,5','5,4','4,5','3,5','5,3'])
        self.assertTrue(all(v is not None for v in out.values())); self.assertEqual(out['5,5'].cost,10)
        self.assertEqual(s.metrics.searches,1); self.assertLessEqual(s.metrics.expansions,40)
if __name__=='__main__': unittest.main()
