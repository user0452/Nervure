from collections import deque

def ancestors(store, start):
    oid = start
    while oid:
        commit = store.get_commit(oid)
        yield commit
        oid = commit.parent

def log(store, start, limit=None):
    out = []
    for commit in ancestors(store, start):
        out.append(commit)
        if limit and len(out) >= limit:
            break
    return out

def common_ancestor(store, left, right):
    left_seen = {c.oid for c in ancestors(store, left)}
    for c in ancestors(store, right):
        if c.oid in left_seen:
            return c.oid
    return None

def walk_breadth_first(store, starts):
    q = deque(starts)
    seen = set()
    while q:
        oid = q.popleft()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        c = store.get_commit(oid)
        yield c
        if c.parent:
            q.append(c.parent)
