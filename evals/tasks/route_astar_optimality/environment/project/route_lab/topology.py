def grid_graph(rows, cols, weight=1.0):
    from .graph import Graph
    g = Graph()
    for r in range(rows):
        for c in range(cols):
            node = f'{r},{c}'
            g.add_node(node)
            if c + 1 < cols:
                g.add_edge(node, f'{r},{c + 1}', weight)
                g.add_edge(f'{r},{c + 1}', node, weight)
            if r + 1 < rows:
                g.add_edge(node, f'{r + 1},{c}', weight)
                g.add_edge(f'{r + 1},{c}', node, weight)
    return g
