import json
from .graph import Graph

def dumps(graph):
    edges = []
    for node in sorted(graph.nodes()):
        for e in graph.outgoing(node):
            edges.append({'id': e.id, 'source': e.source, 'target': e.target, 'weight': e.weight, 'blocked': e.blocked})
    return json.dumps({'edges': edges}, sort_keys=True)

def loads(text):
    g = Graph()
    for row in json.loads(text)['edges']:
        eid = g.add_edge(row['source'], row['target'], row['weight'], row['id'])
        if row.get('blocked'):
            g.set_blocked(eid, True)
    return g
