from dataclasses import dataclass
from .models import Color, PieceType, Position

@dataclass(frozen=True)
class Piece:
    color: Color
    kind: PieceType
ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))

def rook_targets(board, pos, piece):
    out = []
    for dr, dc in ORTHOGONAL:
        cur = pos.offset(dr, dc)
        while cur.in_bounds:
            target = board.at(cur)
            if target is None:
                out.append(cur)
            else:
                if target.color != piece.color:
                    out.append(cur)
                break
            cur = cur.offset(dr, dc)
    return out

def cannon_targets(board, pos, piece):
    out = []
    for dr, dc in ORTHOGONAL:
        cur = pos.offset(dr, dc)
        screen_count = 0
        while cur.in_bounds:
            target = board.at(cur)
            if target is None:
                if screen_count == 0:
                    out.append(cur)
            elif screen_count == 0:
                screen_count = 1
            else:
                if target.color != piece.color and screen_count == 1:
                    out.append(cur)
                break
            cur = cur.offset(dr, dc)
    return out

def horse_targets(board, pos, piece):
    out = []
    patterns = [((-1, 0), (-2, -1)), ((-1, 0), (-2, 1)), ((1, 0), (2, -1)), ((1, 0), (2, 1)), ((0, -1), (-1, -2)), ((0, -1), (1, -2)), ((0, 1), (-1, 2)), ((0, 1), (1, 2))]
    for leg, dest in patterns:
        leg_pos = pos.offset(*leg)
        target_pos = pos.offset(*dest)
        if not target_pos.in_bounds or board.at(leg_pos) is not None:
            continue
        target = board.at(target_pos)
        if target is None or target.color != piece.color:
            out.append(target_pos)
    return out

def elephant_targets(board, pos, piece):
    out = []
    for dr, dc in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
        dst = pos.offset(dr, dc)
        eye = pos.offset(dr // 2, dc // 2)
        if not dst.in_bounds or board.at(eye) is not None:
            continue
        if piece.color is Color.RED and dst.row < 5:
            continue
        if piece.color is Color.BLACK and dst.row > 4:
            continue
        target = board.at(dst)
        if target is None or target.color != piece.color:
            out.append(dst)
    return out

def advisor_targets(board, pos, piece):
    out = []
    rows = range(7, 10) if piece.color is Color.RED else range(0, 3)
    for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        dst = pos.offset(dr, dc)
        if dst.row not in rows or dst.col not in range(3, 6):
            continue
        target = board.at(dst)
        if target is None or target.color != piece.color:
            out.append(dst)
    return out

def king_targets(board, pos, piece):
    out = []
    rows = range(7, 10) if piece.color is Color.RED else range(0, 3)
    for dr, dc in ORTHOGONAL:
        dst = pos.offset(dr, dc)
        if dst.row not in rows or dst.col not in range(3, 6):
            continue
        target = board.at(dst)
        if target is None or target.color != piece.color:
            out.append(dst)
    return out

def pawn_targets(board, pos, piece):
    direction = -1 if piece.color is Color.RED else 1
    crossed = pos.row <= 4 if piece.color is Color.RED else pos.row >= 5
    deltas = [(direction, 0)] + ([(0, -1), (0, 1)] if crossed else [])
    out = []
    for dr, dc in deltas:
        dst = pos.offset(dr, dc)
        if not dst.in_bounds:
            continue
        target = board.at(dst)
        if target is None or target.color != piece.color:
            out.append(dst)
    return out

def pseudo_targets(board, pos, piece):
    dispatch = {PieceType.ROOK: rook_targets, PieceType.CANNON: cannon_targets, PieceType.HORSE: horse_targets, PieceType.ELEPHANT: elephant_targets, PieceType.ADVISOR: advisor_targets, PieceType.KING: king_targets, PieceType.PAWN: pawn_targets}
    return dispatch[piece.kind](board, pos, piece)
