from dataclasses import dataclass, field

@dataclass
class StatusResult:
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)

    def clean(self):
        return not (self.modified or self.deleted or self.untracked or self.renamed)
