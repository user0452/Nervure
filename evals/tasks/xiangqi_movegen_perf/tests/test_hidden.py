import sys, unittest
sys.path.insert(0,'/workspace')
from xiangqi.board import Board
from xiangqi.models import Color
from xiangqi.movegen import generate_all
class Hidden(unittest.TestCase):
    def test_same_moves_with_bounded_scans(self):
        b=Board.initial(); b.reset_metrics(); moves=generate_all(b,Color.RED)
        self.assertGreater(len(moves), 0)
        self.assertLessEqual(b.scan_count, 2, f'full board scanned {b.scan_count} times')
if __name__=='__main__': unittest.main()
