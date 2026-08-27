from .movegen import generate_all

def perft(board, color, depth):
    if depth < 0:
        raise ValueError('depth must be non-negative')
    if depth == 0:
        return 1
    total = 0
    for move in generate_all(board, color):
        child = board.clone()
        child.apply(move)
        total += perft(child, color.opponent, depth - 1)
    return total

def divide(board, color, depth):
    result = {}
    for move in generate_all(board, color):
        child = board.clone()
        child.apply(move)
        result[str(move)] = perft(child, color.opponent, depth - 1)
    return result
