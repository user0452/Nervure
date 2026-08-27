from dataclasses import dataclass, asdict
import json
from pathlib import Path

@dataclass
class RepositoryConfig:
    default_branch: str = 'main'
    user_name: str = 'Nervure User'
    user_email: str = 'user@example.invalid'
    detect_renames: bool = True
    text_diff_limit: int = 2000000

class ConfigStore:

    def __init__(self, root):
        self.path = Path(root) / '.mvcs' / 'config.json'

    def load(self):
        if not self.path.exists():
            return RepositoryConfig()
        raw = json.loads(self.path.read_text())
        return RepositoryConfig(**raw)

    def save(self, config):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(config), indent=2, sort_keys=True))
