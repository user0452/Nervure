class Refs:

    def __init__(self):
        self.branches = {'main': None}
        self.current = 'main'

    @property
    def head(self):
        return self.branches[self.current]

    def update_head(self, oid):
        self.branches[self.current] = oid

    def branch(self, name):
        if name in self.branches:
            raise ValueError('branch exists')
        self.branches[name] = self.head
