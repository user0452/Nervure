import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,'/workspace')
from minivcs.repository import Repository
class Hidden(unittest.TestCase):
    def test_pure_rename(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); r=Repository(root); r.worktree.write('old.txt','same'); r.add('old.txt'); r.commit('base'); (root/'old.txt').rename(root/'new.txt')
            st=r.status(); self.assertEqual(st.renamed,[('old.txt','new.txt')]); self.assertEqual(st.deleted,[]); self.assertEqual(st.untracked,[])
    def test_different_content_not_rename(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); r=Repository(root); r.worktree.write('old.txt','a'); r.add('old.txt'); r.commit('base'); r.worktree.remove('old.txt'); r.worktree.write('new.txt','b')
            st=r.status(); self.assertEqual(st.renamed,[]); self.assertEqual(st.deleted,['old.txt']); self.assertEqual(st.untracked,['new.txt'])
if __name__=='__main__': unittest.main()
