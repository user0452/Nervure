class LandmarkHeuristic:

    def __init__(self, distances):
        self.distances = distances

    def __call__(self, node, goal):
        best = 0.0
        for _, dist in self.distances.items():
            if node in dist and goal in dist:
                best = max(best, abs(dist[goal] - dist[node]))
        return best

def choose_landmarks(graph, count=2):
    nodes = sorted(graph.nodes())
    if not nodes:
        return []
    if count <= 1:
        return [nodes[0]]
    step = max(1, len(nodes) // count)
    return nodes[::step][:count]
