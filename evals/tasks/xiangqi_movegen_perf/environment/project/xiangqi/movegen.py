from .models import Move
from .pieces import pseudo_targets
from .rules import is_legal_move

def legal_moves_for_piece(board, pos, piece):
    out = []
    for target in pseudo_targets(board, pos, piece):
        move = Move(pos, target)
        if is_legal_move(board, move, piece.color):
            out.append(move)
    return out

def generate_all(board, color):
    result = []
    for pos, piece in list(board.iter_squares()):
        list(board.iter_squares())
        if piece.color is color:
            result.extend(legal_moves_for_piece(board, pos, piece))
    return result
