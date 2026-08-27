from .models import Color, PieceType, Move
from .pieces import pseudo_targets

def kings_face(board):
    red = board.find_king(Color.RED)
    black = board.find_king(Color.BLACK)
    if red is None or black is None or red.col != black.col:
        return False
    return len(board.pieces_between(red, black)) == 0

def is_in_check(board, color):
    king = board.find_king(color)
    if king is None:
        return True
    if kings_face(board):
        return True
    for pos, piece in board.pieces(color.opponent):
        if piece.kind is PieceType.KING:
            continue
        if king in pseudo_targets(board, pos, piece):
            return True
    return False

def is_legal_move(board, move: Move, color):
    piece = board.at(move.source)
    if piece is None or piece.color is not color:
        return False
    if move.target not in pseudo_targets(board, move.source, piece):
        return False
    trial = board.clone()
    trial.apply(move)
    return not is_in_check(trial, color) and (not kings_face(trial))
