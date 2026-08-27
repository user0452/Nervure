FILES = 'abcdefghi'

def pos_to_text(pos):
    return f'{FILES[pos.col]}{pos.row}'

def move_to_text(move):
    return f'{pos_to_text(move.source)}-{pos_to_text(move.target)}'
