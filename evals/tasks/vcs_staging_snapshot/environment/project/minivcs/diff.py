import difflib

def text_diff(old: bytes, new: bytes, path='file'):
    try:
        a = old.decode().splitlines(True)
        b = new.decode().splitlines(True)
    except UnicodeDecodeError:
        return [f'Binary files differ: {path}'] if old != new else []
    return list(difflib.unified_diff(a, b, fromfile='a/' + path, tofile='b/' + path))
