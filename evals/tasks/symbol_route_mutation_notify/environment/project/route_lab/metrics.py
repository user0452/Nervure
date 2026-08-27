from dataclasses import dataclass

@dataclass
class SearchMetrics:
    expansions: int = 0
    relaxations: int = 0
    searches: int = 0

    def reset(self):
        self.expansions = self.relaxations = self.searches = 0
