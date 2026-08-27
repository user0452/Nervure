import base64, json

def encode_tree(tree):
    return {p: base64.b64encode(data).decode() for p, data in tree.items()}

def decode_tree(obj):
    return {p: base64.b64decode(data) for p, data in obj.items()}

def dump_commit(commit):
    return json.dumps({'oid': commit.oid, 'message': commit.message, 'parent': commit.parent, 'tree': encode_tree(commit.tree)}, sort_keys=True)
