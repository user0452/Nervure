from __future__ import annotations
from .models import Color, PieceType, Position, Move
from .pieces import Piece

class Board:

    def __init__(self):
        self._grid = {}
        self.scan_count = 0

    @classmethod
    def initial(cls):
        b = cls()
        back = [PieceType.ROOK, PieceType.HORSE, PieceType.ELEPHANT, PieceType.ADVISOR, PieceType.KING, PieceType.ADVISOR, PieceType.ELEPHANT, PieceType.HORSE, PieceType.ROOK]
        for col, kind in enumerate(back):
            b.place(Position(0, col), Piece(Color.BLACK, kind))
            b.place(Position(9, col), Piece(Color.RED, kind))
        for col in (1, 7):
            b.place(Position(2, col), Piece(Color.BLACK, PieceType.CANNON))
            b.place(Position(7, col), Piece(Color.RED, PieceType.CANNON))
        for col in (0, 2, 4, 6, 8):
            b.place(Position(3, col), Piece(Color.BLACK, PieceType.PAWN))
            b.place(Position(6, col), Piece(Color.RED, PieceType.PAWN))
        return b

    def clone(self):
        other = Board()
        other._grid = dict(self._grid)
        return other

    def at(self, pos):
        return self._grid.get(pos)

    def place(self, pos, piece):
        if not pos.in_bounds:
            raise ValueError('position outside board')
        self._grid[pos] = piece

    def remove(self, pos):
        return self._grid.pop(pos, None)

    def apply(self, move: Move):
        piece = self.remove(move.source)
        if piece is None:
            raise ValueError('no piece at source')
        captured = self.remove(move.target)
        self.place(move.target, piece)
        return captured

    def iter_squares(self):
        self.scan_count += 1
        for row in range(10):
            for col in range(9):
                pos = Position(row, col)
                piece = self.at(pos)
                if piece is not None:
                    yield (pos, piece)

    def pieces(self, color=None):
        for pos, piece in self._grid.items():
            if color is None or piece.color is color:
                yield (pos, piece)

    def find_king(self, color):
        for pos, piece in self._grid.items():
            if piece.color is color and piece.kind is PieceType.KING:
                return pos
        return None

    def pieces_between(self, a, b):
        if a.row == b.row:
            lo, hi = sorted((a.col, b.col))
            return [self.at(Position(a.row, c)) for c in range(lo + 1, hi) if self.at(Position(a.row, c))]
        if a.col == b.col:
            lo, hi = sorted((a.row, b.row))
            return [self.at(Position(r, a.col)) for r in range(lo + 1, hi) if self.at(Position(r, a.col))]
        raise ValueError('positions are not aligned')

    def reset_metrics(self):
        self.scan_count = 0
