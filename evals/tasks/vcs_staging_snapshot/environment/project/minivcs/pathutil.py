from pathlib import Path, PurePosixPath

def normalize(root: Path, path):
    p = (root / Path(path)).resolve()
    root = root.resolve()
    try:
        rel = p.relative_to(root)
    except ValueError:
        raise ValueError('path escapes repository')
    if '.mvcs' in rel.parts:
        raise ValueError('metadata path not allowed')
    return PurePosixPath(rel.as_posix()).as_posix()
