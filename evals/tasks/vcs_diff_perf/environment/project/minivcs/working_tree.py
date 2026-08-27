from pathlib import Path
from .pathutil import normalize

class WorkingTree:

    def __init__(self, root, metrics):
        self.root = Path(root)
        self.metrics = metrics

    def read(self, path):
        self.metrics.file_reads += 1
        return (self.root / path).read_bytes()

    def write(self, path, data):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())

    def remove(self, path):
        p = self.root / path
        if p.exists():
            p.unlink()

    def files(self):
        out = []
        for p in self.root.rglob('*'):
            if p.is_file() and '.mvcs' not in p.parts:
                out.append(p.relative_to(self.root).as_posix())
        return sorted(out)
