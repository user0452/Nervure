from .models import PieceType, Color
VALUES = {PieceType.KING: 10000, PieceType.ROOK: 500, PieceType.CANNON: 450, PieceType.HORSE: 300, PieceType.ELEPHANT: 120, PieceType.ADVISOR: 120, PieceType.PAWN: 70}

def material_score(board, perspective=Color.RED):
    score = 0
    for _, piece in board.pieces():
        sign = 1 if piece.color is perspective else -1
        score += sign * VALUES[piece.kind]
    return score
