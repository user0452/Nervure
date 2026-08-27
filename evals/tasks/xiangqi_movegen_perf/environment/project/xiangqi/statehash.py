import hashlib
from .models import Position

def position_key(board, turn=None):
    parts = []
    for pos, piece in sorted(board.pieces(), key=lambda x: (x[0].row, x[0].col)):
        parts.append(f'{pos.row}:{pos.col}:{piece.color.value}:{piece.kind.value}')
    if turn is not None:
        parts.append(f'turn:{turn.value}')
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()

def compact_key(board):
    value = 0
    for pos, piece in board.pieces():
        token = (pos.row * 9 + pos.col + 1) * (list(type(piece.kind)).index(piece.kind) + 3)
        value ^= token << (0 if piece.color.value == 'red' else 7)
    return value

def same_position(a, b):
    return position_key(a) == position_key(b)
