import unittest
from xiangqi.board import Board
from xiangqi.models import Color, PieceType, Position, Move
from xiangqi.pieces import Piece, rook_targets, cannon_targets
from xiangqi.parser import parse_move
from xiangqi.serialization import dump_board, load_board

class BasicTests(unittest.TestCase):

    def test_rook_blocking(self):
        b = Board()
        p = Piece(Color.RED, PieceType.ROOK)
        b.place(Position(5, 4), p)
        b.place(Position(5, 6), Piece(Color.RED, PieceType.PAWN))
        self.assertIn(Position(5, 5), rook_targets(b, Position(5, 4), p))
        self.assertNotIn(Position(5, 6), rook_targets(b, Position(5, 4), p))

    def test_cannon_quiet_move_before_screen(self):
        b = Board()
        p = Piece(Color.RED, PieceType.CANNON)
        b.place(Position(5, 4), p)
        b.place(Position(5, 6), Piece(Color.BLACK, PieceType.PAWN))
        self.assertIn(Position(5, 5), cannon_targets(b, Position(5, 4), p))

    def test_parse(self):
        self.assertEqual(parse_move('a9-a8'), Move(Position(9, 0), Position(8, 0)))

    def test_round_trip(self):
        b = Board.initial()
        self.assertEqual(dump_board(load_board(dump_board(b))), dump_board(b))
if __name__ == '__main__':
    unittest.main()
