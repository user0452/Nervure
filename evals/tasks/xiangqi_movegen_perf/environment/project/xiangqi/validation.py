from dataclasses import dataclass
from .models import Color, PieceType
from .rules import kings_face

@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str

def validate_board(board):
    issues = []
    kings = {Color.RED: 0, Color.BLACK: 0}
    for pos, piece in board.pieces():
        if piece.kind is PieceType.KING:
            kings[piece.color] += 1
        if piece.kind is PieceType.ELEPHANT:
            if piece.color is Color.RED and pos.row < 5:
                issues.append(ValidationIssue('red-elephant-river', 'red elephant crossed river'))
            if piece.color is Color.BLACK and pos.row > 4:
                issues.append(ValidationIssue('black-elephant-river', 'black elephant crossed river'))
        if piece.kind in {PieceType.KING, PieceType.ADVISOR}:
            rows = range(7, 10) if piece.color is Color.RED else range(0, 3)
            if pos.row not in rows or pos.col not in range(3, 6):
                issues.append(ValidationIssue('palace', 'palace piece outside palace'))
    for color, count in kings.items():
        if count != 1:
            issues.append(ValidationIssue('king-count', f'{color.value} has {count} kings'))
    if kings_face(board):
        issues.append(ValidationIssue('flying-general', 'kings face each other'))
    return issues
