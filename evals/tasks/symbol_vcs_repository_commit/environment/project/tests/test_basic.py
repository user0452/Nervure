import tempfile, unittest
from pathlib import Path
from minivcs.repository import Repository

class Basic(unittest.TestCase):

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.r = Repository(self.root)

    def tearDown(self):
        self.t.cleanup()

    def test_commit_and_modify(self):
        self.r.worktree.write('a.txt', 'one\n')
        self.r.add('a.txt')
        c = self.r.commit('first')
        self.assertEqual(self.r.checkout_bytes(c.oid, 'a.txt'), b'one\n')
        self.r.worktree.write('a.txt', 'two\n')
        self.assertEqual(self.r.status().modified, ['a.txt'])

    def test_clean_after_commit(self):
        self.r.worktree.write('a.txt', 'x')
        self.r.add('a.txt')
        self.r.commit('x')
        self.assertTrue(self.r.status().clean())

    def test_diff(self):
        self.r.worktree.write('a.txt', 'x\n')
        self.r.add('a.txt')
        self.r.commit('x')
        self.r.worktree.write('a.txt', 'y\n')
        self.assertTrue(self.r.diff('a.txt'))
if __name__ == '__main__':
    unittest.main()
