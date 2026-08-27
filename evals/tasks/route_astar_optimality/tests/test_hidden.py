import sys,unittest
sys.path.insert(0,'/workspace')
from route_lab.graph import Graph
from route_lab.service import RouteService
from route_lab.heuristics import TableHeuristic
class Hidden(unittest.TestCase):
    def test_inconsistent_admissible_heuristic_still_optimal(self):
        g=Graph(); g.add_edge('S','A',2); g.add_edge('S','B',2); g.add_edge('A','C',2); g.add_edge('B','C',1); g.add_edge('C','G',2)
        h=TableHeuristic({'A':2,'B':3,'C':0,'S':0,'G':0})
        res=RouteService(g).route('S','G',algorithm='astar',heuristic=h,use_cache=False)
        self.assertEqual(res.nodes,['S','B','C','G']); self.assertEqual(res.cost,5)
if __name__=='__main__': unittest.main()
