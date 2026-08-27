from dataclasses import dataclass
from .statehash import position_key

@dataclass(frozen=True)
class HistoryEntry:
    move: object
    captured: object
    before_key: str
    after_key: str

class MoveHistory:

    def __init__(self):
        self._entries = []

    def record(self, before, move, captured, after):
        self._entries.append(HistoryEntry(move, captured, position_key(before), position_key(after)))

    def pop(self):
        if not self._entries:
            raise IndexError('history is empty')
        return self._entries.pop()

    def last(self, n=1):
        return self._entries[-n:]

    def __len__(self):
        return len(self._entries)

    def keys(self):
        if not self._entries:
            return []
        return [self._entries[0].before_key] + [e.after_key for e in self._entries]

    def repeated(self, key, count=3):
        return self.keys().count(key) >= count
