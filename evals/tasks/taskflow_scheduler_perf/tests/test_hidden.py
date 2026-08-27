import sys,unittest
sys.path.insert(0,'/workspace')
from taskflow.graph import TaskGraph
from taskflow.scheduler import Scheduler
from taskflow.executor import DeterministicExecutor
class Hidden(unittest.TestCase):
    def test_large_chain_does_not_full_scan_each_step(self):
        g=TaskGraph(); n=80
        for i in range(n): g.add_task(f'T{i:03}')
        for i in range(1,n): g.add_dependency(f'T{i:03}',f'T{i-1:03}')
        ex=DeterministicExecutor(); s=Scheduler(g,ex); s.run_all(); self.assertEqual(len(ex.calls),n); self.assertLessEqual(s.full_scan_count,3, f'full scans={s.full_scan_count}')
if __name__=='__main__': unittest.main()
