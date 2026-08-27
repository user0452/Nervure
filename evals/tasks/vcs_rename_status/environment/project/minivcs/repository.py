from pathlib import Path
from .metrics import Metrics
from .working_tree import WorkingTree
from .index import Index
from .object_store import ObjectStore
from .refs import Refs
from .objects import Commit
from .hashing import hash_bytes
from .status import StatusResult
from .diff import text_diff

class Repository:

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.metrics = Metrics()
        self.worktree = WorkingTree(self.root, self.metrics)
        self.index = Index()
        self.store = ObjectStore(self.root)
        self.refs = Refs()
        self._counter = 0

    def add(self, path):
        path = str(path).replace('\\', '/')
        self.index.add(path, self.worktree.read(path))

    def add_all(self):
        for path in self.worktree.files():
            self.add(path)

    def commit(self, message):
        if not self.index.entries:
            raise ValueError('nothing staged')
        self._counter += 1
        payload = '|'.join((f'{p}:{hash_bytes(v, self.metrics)}' for p, v in sorted(self.index.entries.items())))
        oid = hash_bytes(f'{self._counter}|{message}|{payload}'.encode(), self.metrics)
        commit = Commit.build(oid, message, dict(self.index.entries), self.refs.head)
        self.store.put_commit(commit)
        self.refs.update_head(oid)
        return commit

    def head_commit(self):
        return self.store.get_commit(self.refs.head) if self.refs.head else None

    def _work_snapshot(self):
        result = {}
        for path in self.worktree.files():
            data = self.worktree.read(path)
            result[path] = (data, hash_bytes(data, self.metrics))
        return result

    def status(self):
        head = self.head_commit()
        tracked = head.tree if head else {}
        snap = self._work_snapshot()
        result = StatusResult()
        tracked_hash = {p: hash_bytes(data, self.metrics) for p, data in tracked.items()}
        deleted = [p for p in tracked if p not in snap]
        untracked = [p for p in snap if p not in tracked]
        for p in tracked:
            if p in snap and tracked_hash[p] != snap[p][1]:
                result.modified.append(p)
        by_hash = {}
        result.deleted = sorted(deleted)
        result.untracked = sorted(untracked)
        result.modified.sort()
        result.renamed.sort()
        return result

    def diff(self, path):
        head = self.head_commit()
        old = head.tree.get(path, b'') if head else b''
        new = self.worktree.read(path) if (self.root / path).exists() else b''
        return text_diff(old, new, path)

    def checkout_bytes(self, oid, path):
        return self.store.get_commit(oid).tree[path]
