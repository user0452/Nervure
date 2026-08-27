import sys, unittest
sys.path.insert(0, '/workspace')
from xiangqi.board import Board
from xiangqi.models import Color, PieceType, Position, Move
from xiangqi.pieces import Piece
from xiangqi.rules import kings_face, is_legal_move
class Hidden(unittest.TestCase):
    def board(self, blocker=False):
        b=Board(); b.place(Position(9,4),Piece(Color.RED,PieceType.KING)); b.place(Position(0,4),Piece(Color.BLACK,PieceType.KING))
        if blocker: b.place(Position(5,4),Piece(Color.RED,PieceType.PAWN))
        return b
    def test_open_file_faces(self): self.assertTrue(kings_face(self.board(False)))
    def test_blocker_prevents_face(self): self.assertFalse(kings_face(self.board(True)))
    def test_move_exposing_file_is_illegal(self):
        b=self.board(True)
        self.assertFalse(is_legal_move(b, Move(Position(5,4),Position(5,3)), Color.RED))
if __name__=='__main__': unittest.main()
