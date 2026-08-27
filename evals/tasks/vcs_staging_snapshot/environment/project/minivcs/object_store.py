import json
from pathlib import Path

class ObjectStore:

    def __init__(self, root):
        self.root = Path(root) / '.mvcs' / 'objects'
        self.root.mkdir(parents=True, exist_ok=True)
        self.commits = {}

    def put_commit(self, commit):
        self.commits[commit.oid] = commit

    def get_commit(self, oid):
        return self.commits[oid]
