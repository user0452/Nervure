from .board import Board
from .models import Color
from .movegen import generate_all
from .rules import is_legal_move

class Game:

    def __init__(self, board=None, turn=Color.RED):
        self.board = board or Board.initial()
        self.turn = turn
        self.history = []

    def legal_moves(self):
        return generate_all(self.board, self.turn)

    def play(self, move):
        if not is_legal_move(self.board, move, self.turn):
            raise ValueError('illegal move')
        captured = self.board.apply(move)
        self.history.append((move, captured))
        self.turn = self.turn.opponent
        return captured

    @property
    def finished(self):
        return self.board.find_king(Color.RED) is None or self.board.find_king(Color.BLACK) is None
