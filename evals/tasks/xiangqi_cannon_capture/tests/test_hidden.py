import sys, unittest
sys.path.insert(0,'/workspace')
from xiangqi.board import Board
from xiangqi.models import Color, PieceType, Position
from xiangqi.pieces import Piece, cannon_targets
class Hidden(unittest.TestCase):
    def test_zero_screen_cannot_capture(self):
        b=Board(); c=Piece(Color.RED,PieceType.CANNON); b.place(Position(5,0),c); b.place(Position(5,4),Piece(Color.BLACK,PieceType.ROOK))
        self.assertNotIn(Position(5,4), cannon_targets(b,Position(5,0),c))
    def test_exactly_one_screen_can_capture(self):
        b=Board(); c=Piece(Color.RED,PieceType.CANNON); b.place(Position(5,0),c); b.place(Position(5,2),Piece(Color.RED,PieceType.PAWN)); b.place(Position(5,4),Piece(Color.BLACK,PieceType.ROOK))
        self.assertIn(Position(5,4), cannon_targets(b,Position(5,0),c))
    def test_two_screens_cannot_capture(self):
        b=Board(); c=Piece(Color.RED,PieceType.CANNON); b.place(Position(5,0),c); b.place(Position(5,1),Piece(Color.RED,PieceType.PAWN)); b.place(Position(5,2),Piece(Color.BLACK,PieceType.PAWN)); b.place(Position(5,4),Piece(Color.BLACK,PieceType.ROOK))
        self.assertNotIn(Position(5,4), cannon_targets(b,Position(5,0),c))
if __name__=='__main__': unittest.main()
