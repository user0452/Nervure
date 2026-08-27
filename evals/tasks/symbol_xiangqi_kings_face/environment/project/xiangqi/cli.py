from .game import Game
from .parser import parse_move
from .notation import move_to_text

def render(board):
    from .models import Position
    lines = []
    for r in range(10):
        row = []
        for c in range(9):
            p = board.at(Position(r, c))
            row.append('.' if p is None else p.kind.value[0].upper() if p.color.value == 'red' else p.kind.value[0])
        lines.append(' '.join(row))
    return '\n'.join(lines)

def main():
    game = Game()
    while not game.finished:
        print(render(game.board))
        raw = input(f'{game.turn.value}> ')
        if raw in {'quit', 'exit'}:
            break
        try:
            mv = parse_move(raw)
            game.play(mv)
            print('played', move_to_text(mv))
        except Exception as exc:
            print('error:', exc)
if __name__ == '__main__':
    main()
