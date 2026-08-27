import argparse
from .repository import Repository

def build_parser():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('status')
    a.add_argument('root')
    a = sub.add_parser('add')
    a.add_argument('root')
    a.add_argument('paths', nargs='+')
    return p

def main(argv=None):
    ns = build_parser().parse_args(argv)
    repo = Repository(ns.root)
    if ns.cmd == 'status':
        print(repo.status())
    elif ns.cmd == 'add':
        for path in ns.paths:
            repo.add(path)
if __name__ == '__main__':
    main()
