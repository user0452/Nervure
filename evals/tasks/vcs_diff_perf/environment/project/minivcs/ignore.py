from fnmatch import fnmatch
from pathlib import PurePosixPath

class IgnoreRules:

    def __init__(self, patterns=()):
        self.patterns = [p.strip() for p in patterns if p.strip() and (not p.lstrip().startswith('#'))]

    @classmethod
    def parse(cls, text):
        return cls(text.splitlines())

    def ignores(self, path):
        path = PurePosixPath(path).as_posix()
        matched = False
        for pattern in self.patterns:
            negate = pattern.startswith('!')
            pat = pattern[1:] if negate else pattern
            if pat.endswith('/') and path.startswith(pat.rstrip('/') + '/'):
                matched = not negate
            elif fnmatch(path, pat) or fnmatch(PurePosixPath(path).name, pat):
                matched = not negate
        return matched

    def filter(self, paths):
        return [p for p in paths if not self.ignores(p)]
