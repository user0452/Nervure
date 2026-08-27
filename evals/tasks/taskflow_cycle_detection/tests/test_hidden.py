import sys,unittest
sys.path.insert(0,'/workspace')
from taskflow.graph import TaskGraph
class Hidden(unittest.TestCase):
    def test_three_node_cycle_rejected(self):
        g=TaskGraph(); [g.add_task(x) for x in 'ABC']; g.add_dependency('A','B'); g.add_dependency('B','C')
        with self.assertRaises(ValueError): g.add_dependency('C','A')
    def test_longer_cycle_rejected(self):
        g=TaskGraph(); [g.add_task(x) for x in 'ABCDE']; g.add_dependency('B','A'); g.add_dependency('C','B'); g.add_dependency('D','C'); g.add_dependency('E','D')
        with self.assertRaises(ValueError): g.add_dependency('A','E')
if __name__=='__main__': unittest.main()
