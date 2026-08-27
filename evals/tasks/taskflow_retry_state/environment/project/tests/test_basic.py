import unittest
from taskflow.graph import TaskGraph
from taskflow.scheduler import Scheduler
from taskflow.executor import DeterministicExecutor
from taskflow.model import TaskState

class Basic(unittest.TestCase):

    def test_dependency_order(self):
        g = TaskGraph()
        [g.add_task(x) for x in 'ABC']
        g.add_dependency('B', 'A')
        g.add_dependency('C', 'B')
        ex = DeterministicExecutor()
        s = Scheduler(g, ex)
        s.run_all()
        self.assertEqual(ex.calls, ['A', 'B', 'C'])

    def test_failure_blocks_downstream(self):
        g = TaskGraph()
        [g.add_task(x) for x in 'AB']
        g.add_dependency('B', 'A')
        s = Scheduler(g, DeterministicExecutor({'A': [False]}))
        s.run_all()
        self.assertIs(g.tasks['B'].state, TaskState.BLOCKED)

    def test_topological(self):
        g = TaskGraph()
        [g.add_task(x) for x in 'ABC']
        g.add_dependency('C', 'A')
        g.add_dependency('C', 'B')
        self.assertEqual(g.topological()[-1], 'C')
if __name__ == '__main__':
    unittest.main()
