"""象棋局面合法性检查（与 Pikafish 2026-09 的输入校验对齐）。

Pikafish 新版收到以下局面会输出 CRITICAL ERROR 并直接退出进程：
  - 仕/相/兵 落在规则上不可能到达的位置
  - 将帅照面
  - 不该走的一方的将/帅正处于被攻击状态（King can be captured）
所以在送进引擎前先在这里检查，并据此修正"轮到谁走"。

坐标：grid[row][col]，红方在下（row 0 = 黑方底线，row 9 = 红方底线），大写 = 红方。
"""
from __future__ import annotations

RED_ADVISOR = {(9, 3), (9, 5), (8, 4), (7, 3), (7, 5)}
BLACK_ADVISOR = {(0, 3), (0, 5), (1, 4), (2, 3), (2, 5)}
RED_BISHOP = {(9, 2), (9, 6), (7, 0), (7, 4), (7, 8), (5, 2), (5, 6)}
BLACK_BISHOP = {(0, 2), (0, 6), (2, 0), (2, 4), (2, 8), (4, 2), (4, 6)}


def placement_errors(g) -> list[str]:
    errs = []
    for r in range(10):
        for c in range(9):
            p = g[r][c]
            if p == ' ':
                continue
            if p == 'K' and not (7 <= r <= 9 and 3 <= c <= 5):
                errs.append(f'帅不在九宫 ({r},{c})')
            elif p == 'k' and not (0 <= r <= 2 and 3 <= c <= 5):
                errs.append(f'将不在九宫 ({r},{c})')
            elif p == 'A' and (r, c) not in RED_ADVISOR:
                errs.append(f'仕位置非法 ({r},{c})')
            elif p == 'a' and (r, c) not in BLACK_ADVISOR:
                errs.append(f'士位置非法 ({r},{c})')
            elif p == 'B' and (r, c) not in RED_BISHOP:
                errs.append(f'相位置非法 ({r},{c})')
            elif p == 'b' and (r, c) not in BLACK_BISHOP:
                errs.append(f'象位置非法 ({r},{c})')
            elif p == 'P' and (r > 6 or (r >= 5 and c % 2)):
                errs.append(f'兵位置非法 ({r},{c})')
            elif p == 'p' and (r < 3 or (r <= 4 and c % 2)):
                errs.append(f'卒位置非法 ({r},{c})')
    return errs


def find_king(g, red: bool):
    k = 'K' if red else 'k'
    for r in range(10):
        for c in range(9):
            if g[r][c] == k:
                return r, c
    return None


def kings_facing(g) -> bool:
    rk, bk = find_king(g, True), find_king(g, False)
    if not rk or not bk or rk[1] != bk[1]:
        return False
    c = rk[1]
    return all(g[r][c] == ' ' for r in range(bk[0] + 1, rk[0]))


def attacked(g, r, c, by_red: bool) -> bool:
    """格子 (r,c) 是否被 by_red 一方的车/炮/马/兵攻击（将帅照面另算）。"""
    def own(p):
        return p != ' ' and (p.isupper() == by_red)

    R, C, N, P = ('R', 'C', 'N', 'P') if by_red else ('r', 'c', 'n', 'p')
    # 车 / 炮：四个方向直线扫描
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc, screens = r + dr, c + dc, 0
        while 0 <= rr <= 9 and 0 <= cc <= 8:
            p = g[rr][cc]
            if p != ' ':
                if screens == 0 and p == R:
                    return True
                if screens == 1 and p == C:
                    return True
                screens += 1
                if screens > 1:
                    break
            rr += dr
            cc += dc
    # 马：从目标格反推马的位置，并检查蹩马腿
    for dr, dc in ((2, 1), (2, -1), (-2, 1), (-2, -1), (1, 2), (1, -2), (-1, 2), (-1, -2)):
        nr, nc = r + dr, c + dc
        if not (0 <= nr <= 9 and 0 <= nc <= 8) or g[nr][nc] != N:
            continue
        if abs(dr) == 2:
            leg = (nr - (1 if dr > 0 else -1), nc)
        else:
            leg = (nr, nc - (1 if dc > 0 else -1))
        if g[leg[0]][leg[1]] == ' ':
            return True
    # 兵：红兵向上（row 变小）攻击，过河后可横向；黑卒相反
    if by_red:
        if r + 1 <= 9 and g[r + 1][c] == P:
            return True
        for cc in (c - 1, c + 1):
            if 0 <= cc <= 8 and g[r][cc] == P and r <= 4:
                return True
    else:
        if r - 1 >= 0 and g[r - 1][c] == P:
            return True
        for cc in (c - 1, c + 1):
            if 0 <= cc <= 8 and g[r][cc] == P and r >= 5:
                return True
    return False


def in_check(g, red: bool) -> bool:
    k = find_king(g, red)
    return bool(k) and attacked(g, k[0], k[1], by_red=not red)


def resolve_side(g, preferred: str) -> tuple[str | None, str]:
    """返回 (实际行棋方, 说明)。行棋方为 None 表示局面本身不合法，不能送进引擎。

    preferred 是根据走子推断出的行棋方；若与"将军"状态矛盾则自动纠正：
    一方正被将军 -> 只可能轮到这一方走。
    """
    errs = placement_errors(g)
    if errs:
        return None, '；'.join(errs[:3])
    if kings_facing(g):
        return None, '将帅照面'
    red_checked, black_checked = in_check(g, True), in_check(g, False)
    if red_checked and black_checked:
        return None, '双方同时被将军'
    if red_checked:
        return 'red', '红方被将军' + ('' if preferred == 'red' else '，纠正为轮到红方')
    if black_checked:
        return 'black', '黑方被将军' + ('' if preferred == 'black' else '，纠正为轮到黑方')
    return preferred, ''
