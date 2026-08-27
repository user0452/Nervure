from dataclasses import dataclass

@dataclass(frozen=True)
class PatchHunk:
    path: str
    old: bytes | None
    new: bytes | None

def make_patch(old_tree, new_tree):
    hunks = []
    for path in sorted(set(old_tree) | set(new_tree)):
        old = old_tree.get(path)
        new = new_tree.get(path)
        if old != new:
            hunks.append(PatchHunk(path, old, new))
    return hunks

def apply_patch(tree, hunks, reverse=False):
    out = dict(tree)
    for h in hunks:
        value = h.old if reverse else h.new
        if value is None:
            out.pop(h.path, None)
        else:
            out[h.path] = value
    return out
