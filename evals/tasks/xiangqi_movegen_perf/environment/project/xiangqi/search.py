from dataclasses import dataclass
from .evaluation import material_score
from .movegen import generate_all

@dataclass
class SearchResult:
    move: object
    score: int
    nodes: int

class AlphaBetaSearcher:

    def __init__(self, perspective):
        self.perspective = perspective
        self.nodes = 0

    def evaluate(self, board):
        return material_score(board, self.perspective)

    def choose(self, board, color, depth=2):
        self.nodes = 0
        best_move = None
        best_score = -10 ** 9 if color is self.perspective else 10 ** 9
        for move in generate_all(board, color):
            child = board.clone()
            child.apply(move)
            score = self._search(child, color.opponent, depth - 1, -10 ** 9, 10 ** 9)
            if color is self.perspective:
                if score > best_score:
                    best_score, best_move = (score, move)
            elif score < best_score:
                best_score, best_move = (score, move)
        return SearchResult(best_move, best_score, self.nodes)

    def _search(self, board, color, depth, alpha, beta):
        self.nodes += 1
        if depth <= 0:
            return self.evaluate(board)
        moves = generate_all(board, color)
        if not moves:
            return self.evaluate(board)
        maximizing = color is self.perspective
        if maximizing:
            value = -10 ** 9
            for move in moves:
                child = board.clone()
                child.apply(move)
                value = max(value, self._search(child, color.opponent, depth - 1, alpha, beta))
                alpha = max(alpha, value)
                if alpha >= beta:
                    break
            return value
        value = 10 ** 9
        for move in moves:
            child = board.clone()
            child.apply(move)
            value = min(value, self._search(child, color.opponent, depth - 1, alpha, beta))
            beta = min(beta, value)
            if alpha >= beta:
                break
        return value
