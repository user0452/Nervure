import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,'/workspace')
from minivcs.repository import Repository
class Hidden(unittest.TestCase):
    def test_status_uses_single_worktree_read_per_file(self):
        with tempfile.TemporaryDirectory() as td:
            r=Repository(Path(td))
            for i in range(40): r.worktree.write(f'f{i}.txt',f'value-{i}')
            r.add_all(); r.commit('base'); r.metrics.reset(); st=r.status(); self.assertTrue(st.clean())
            self.assertLessEqual(r.metrics.file_reads,45, r.metrics)
            self.assertLessEqual(r.metrics.hashes,85, r.metrics)
if __name__=='__main__': unittest.main()
