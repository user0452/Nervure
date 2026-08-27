from .models import Color, PieceType, Position
from .pieces import Piece
from .board import Board
CODES = {PieceType.KING: 'k', PieceType.ADVISOR: 'a', PieceType.ELEPHANT: 'e', PieceType.HORSE: 'h', PieceType.ROOK: 'r', PieceType.CANNON: 'c', PieceType.PAWN: 'p'}
REV = {v: k for k, v in CODES.items()}

def dump_board(board):
    rows = []
    for r in range(10):
        cells = []
        for c in range(9):
            p = board.at(Position(r, c))
            if p is None:
                cells.append('.')
            else:
                code = CODES[p.kind]
                cells.append(code.upper() if p.color is Color.RED else code)
        rows.append(''.join(cells))
    return '/'.join(rows)

def load_board(text):
    b = Board()
    rows = text.strip().split('/')
    if len(rows) != 10 or any((len(x) != 9 for x in rows)):
        raise ValueError('invalid board encoding')
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch == '.':
                continue
            color = Color.RED if ch.isupper() else Color.BLACK
            b.place(Position(r, c), Piece(color, REV[ch.lower()]))
    return b
