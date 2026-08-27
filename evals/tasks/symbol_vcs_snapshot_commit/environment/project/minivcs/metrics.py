from dataclasses import dataclass

@dataclass
class Metrics:
    file_reads: int = 0
    hashes: int = 0

    def reset(self):
        self.file_reads = 0
        self.hashes = 0
