from dataclasses import dataclass, field

@dataclass
class TreeNode:
    name: str
    files: dict = field(default_factory=dict)
    children: dict = field(default_factory=dict)

    def add(self, path, data):
        parts = path.split('/')
        node = self
        for part in parts[:-1]:
            node = node.children.setdefault(part, TreeNode(part))
        node.files[parts[-1]] = data

    def flatten(self, prefix=''):
        out = {}
        for name, data in self.files.items():
            out[f'{prefix}{name}'] = data
        for name, child in self.children.items():
            out.update(child.flatten(f'{prefix}{name}/'))
        return out

def build_tree(entries):
    root = TreeNode('')
    for path, data in entries.items():
        root.add(path, data)
    return root
