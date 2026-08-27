from dataclasses import dataclass, field

@dataclass
class MergeResult:
    tree: dict
    conflicts: list[str] = field(default_factory=list)

def three_way_merge(base, left, right):
    result = {}
    conflicts = []
    paths = set(base) | set(left) | set(right)
    for path in sorted(paths):
        b = base.get(path)
        l = left.get(path)
        r = right.get(path)
        if l == r:
            result[path] = l
        elif l == b:
            result[path] = r
        elif r == b:
            result[path] = l
        elif l is None and r is None:
            continue
        else:
            conflicts.append(path)
            if l is not None:
                result[path] = l
    return MergeResult({p: v for p, v in result.items() if v is not None}, conflicts)

def merge_commits(store, base_oid, left_oid, right_oid):
    return three_way_merge(store.get_commit(base_oid).tree, store.get_commit(left_oid).tree, store.get_commit(right_oid).tree)
