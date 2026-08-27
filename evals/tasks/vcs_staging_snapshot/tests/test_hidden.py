import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,'/workspace')
from minivcs.repository import Repository
class Hidden(unittest.TestCase):
    def test_old_commit_does_not_change_after_later_stage(self):
        with tempfile.TemporaryDirectory() as td:
            r=Repository(Path(td)); r.worktree.write('a.txt','v1'); r.add('a.txt'); c1=r.commit('one')
            r.worktree.write('a.txt','v2'); r.add('a.txt')
            self.assertEqual(r.checkout_bytes(c1.oid,'a.txt'), b'v1')
    def test_new_path_does_not_appear_in_old_commit(self):
        with tempfile.TemporaryDirectory() as td:
            r=Repository(Path(td)); r.worktree.write('a','a'); r.add('a'); c1=r.commit('one'); r.worktree.write('b','b'); r.add('b')
            self.assertNotIn('b', r.store.get_commit(c1.oid).tree)
if __name__=='__main__': unittest.main()
