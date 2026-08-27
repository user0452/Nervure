class Index:

    def __init__(self):
        self.entries = {}

    def add(self, path, data):
        self.entries[path] = data

    def remove(self, path):
        self.entries.pop(path, None)

    def snapshot(self):
        return dict(self.entries)
