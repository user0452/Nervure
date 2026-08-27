from .models import Position, Move
from .notation import FILES

def parse_position(text):
    text = text.strip().lower()
    if len(text) != 2 or text[0] not in FILES or (not text[1].isdigit()):
        raise ValueError('invalid position')
    pos = Position(int(text[1]), FILES.index(text[0]))
    if not pos.in_bounds:
        raise ValueError('invalid position')
    return pos

def parse_move(text):
    left, right = text.strip().split('-', 1)
    return Move(parse_position(left), parse_position(right))
