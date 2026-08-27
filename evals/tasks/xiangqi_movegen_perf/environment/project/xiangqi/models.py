from dataclasses import dataclass
from enum import Enum

class Color(str, Enum):
    RED = 'red'
    BLACK = 'black'

    @property
    def opponent(self):
        return Color.BLACK if self is Color.RED else Color.RED

class PieceType(str, Enum):
    KING = 'king'
    ADVISOR = 'advisor'
    ELEPHANT = 'elephant'
    HORSE = 'horse'
    ROOK = 'rook'
    CANNON = 'cannon'
    PAWN = 'pawn'

@dataclass(frozen=True, order=True)
class Position:
    row: int
    col: int

    def offset(self, dr: int, dc: int):
        return Position(self.row + dr, self.col + dc)

    @property
    def in_bounds(self):
        return 0 <= self.row < 10 and 0 <= self.col < 9

@dataclass(frozen=True)
class Move:
    source: Position
    target: Position

    def __str__(self):
        return f'{self.source.row},{self.source.col}->{self.target.row},{self.target.col}'
