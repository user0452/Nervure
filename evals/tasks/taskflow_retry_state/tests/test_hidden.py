import sys,unittest
sys.path.insert(0,'/workspace')
from taskflow.graph import TaskGraph
from taskflow.scheduler import Scheduler
from taskflow.executor import DeterministicExecutor
from taskflow.model import TaskState
class Hidden(unittest.TestCase):
    def test_retry_unblocks_chain(self):
        g=TaskGraph(); [g.add_task(x) for x in 'ABC']; g.add_dependency('B','A'); g.add_dependency('C','B'); ex=DeterministicExecutor({'A':[False,True]}); s=Scheduler(g,ex); s.run_all(); self.assertIs(g.tasks['A'].state,TaskState.FAILED); self.assertIs(g.tasks['B'].state,TaskState.BLOCKED)
        s.retry('A'); s.run_all(); self.assertEqual([g.tasks[x].state for x in 'ABC'],[TaskState.SUCCESS]*3)
    def test_other_failed_prerequisite_keeps_blocked(self):
        g=TaskGraph(); [g.add_task(x) for x in 'ABC']; g.add_dependency('C','A'); g.add_dependency('C','B'); ex=DeterministicExecutor({'A':[False,True],'B':[False]}); s=Scheduler(g,ex); s.run_all(); s.retry('A'); s.run_all(); self.assertIs(g.tasks['C'].state,TaskState.BLOCKED)
if __name__=='__main__': unittest.main()
