from collections import Counter
from .statehash import position_key

class RepetitionTracker:

    def __init__(self):
        self._counts = Counter()
        self._stack = []

    def push(self, board, turn=None):
        key = position_key(board, turn)
        self._stack.append(key)
        self._counts[key] += 1
        return key

    def pop(self):
        key = self._stack.pop()
        self._counts[key] -= 1
        if self._counts[key] <= 0:
            del self._counts[key]
        return key

    def count(self, board, turn=None):
        return self._counts[position_key(board, turn)]

    def is_threefold(self, board, turn=None):
        return self.count(board, turn) >= 3

    def reset(self):
        self._counts.clear()
        self._stack.clear()
