import argparse
from .serialization import loads
from .service import RouteService

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('graph')
    p.add_argument('source')
    p.add_argument('target')
    ns = p.parse_args(argv)
    with open(ns.graph) as f:
        g = loads(f.read())
    print(RouteService(g).route(ns.source, ns.target))
if __name__ == '__main__':
    main()
