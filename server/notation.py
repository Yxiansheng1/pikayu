"""UCI 着法 -> 中文记法（如 h2e2 -> 炮二平五）。

坐标约定（与 FEN/皮卡鱼一致）：列 a..i 从红方左到右，行 0..9 从红方底线到黑方底线。
FEN 第一段（棋盘）首行 = 第 9 行。
"""
from __future__ import annotations

RED_NUM = '九八七六五四三二一'      # 红方：列 a..i 对应 九..一（从红方右往左数）
BLACK_NUM = '１２３４５６７８９'    # 黑方：列 a..i 对应 1..9（从黑方右往左数），用全角数字
NAMES = {'K': '帅', 'A': '仕', 'B': '相', 'N': '马', 'R': '车', 'C': '炮', 'P': '兵',
         'k': '将', 'a': '士', 'b': '象', 'n': '马', 'r': '车', 'c': '炮', 'p': '卒'}
DIAGONAL = set('ABNabn')  # 斜走棋子：进/退后面跟目标列；其余跟步数


def parse_board(fen_board: str) -> list[list[str]]:
    """返回 board[rank][file]，rank 0 = 红方底线。"""
    rows = []
    for r in fen_board.split('/'):
        row = []
        for ch in r:
            row.extend([' '] * int(ch)) if ch.isdigit() else row.append(ch)
        rows.append(row)
    return rows[::-1]


def _sq(s: str) -> tuple[int, int]:
    return ord(s[0]) - ord('a'), int(s[1:])


def uci_to_chinese(fen_board: str, move: str) -> str:
    if not move:
        return ''
    b = parse_board(fen_board)
    (f0, r0), (f1, r1) = _sq(move[:2]), _sq(move[2:])
    piece = b[r0][f0]
    if piece == ' ':
        return move
    red = piece.isupper()
    nums = RED_NUM if red else BLACK_NUM
    name = NAMES[piece]

    # 同一列有同种棋子时用 前/中/后 区分（从行棋方视角，"前"= 更靠近对方）
    same = [r for r in range(10) if b[r][f0] == piece]
    if len(same) >= 2:
        order = sorted(same, reverse=red)  # 红方：行号大的在前；黑方：行号小的在前
        idx = order.index(r0)
        tags = ['前', '后'] if len(same) == 2 else (['前', '中', '后'] if len(same) == 3 else
                                                     ['一', '二', '三', '四', '五'][:len(same)])
        head = tags[idx] + name
    else:
        head = name + nums[f0]

    forward = (r1 > r0) if red else (r1 < r0)
    if r1 == r0:
        return f'{head}平{nums[f1]}'
    action = '进' if forward else '退'
    if piece in DIAGONAL:
        return f'{head}{action}{nums[f1]}'
    steps = abs(r1 - r0)
    step_txt = RED_NUM[9 - steps] if red else BLACK_NUM[steps - 1]
    return f'{head}{action}{step_txt}'


def pv_to_chinese(fen_board: str, red_to_move: bool, pv: list[str], limit: int = 4) -> list[str]:
    """把主要变例逐步翻译，需要逐步落子更新棋盘。"""
    b = parse_board(fen_board)
    out = []
    for mv in pv[:limit]:
        board_str = '/'.join(_row_to_fen(row) for row in b[::-1])
        out.append(uci_to_chinese(board_str, mv))
        (f0, r0), (f1, r1) = _sq(mv[:2]), _sq(mv[2:])
        b[r1][f1], b[r0][f0] = b[r0][f0], ' '
    return out


def _row_to_fen(row) -> str:
    s, n = '', 0
    for c in row:
        if c == ' ':
            n += 1
        else:
            s += (str(n) if n else '') + c
            n = 0
    return s + (str(n) if n else '')
