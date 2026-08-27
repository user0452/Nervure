from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass
class Commit:
    oid: str
    message: str
    tree: dict
    parent: str | None = None
    created_at: str = ''

    @classmethod
    def build(cls, oid, message, tree, parent=None):
        return cls(oid, message, tree, parent, datetime.now(timezone.utc).isoformat())
