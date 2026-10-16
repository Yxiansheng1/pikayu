"""核心流程：截图 -> 识别 -> 引擎 -> 中文提示。与 Web 框架无关，便于单独测试。"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict

from PIL import Image

from engine import Engine
from notation import uci_to_chinese, pv_to_chinese
from recognizer import Recognizer


@dataclass
class Hint:
    ok: bool
    text: str                 # 给通知直接显示的一行字
    move: str = ''            # 中文记法，如 炮二平五
    uci: str = ''
    side: str = ''            # 'red' / 'black'，即截图者（下方一方）
    score: str = ''           # 以截图者视角，如 +0.35 / 杀3步
    pv: list | None = None
    fen: str = ''
    elapsed_ms: int = 0
    error: str = ''


class HintService:
    def __init__(self, recognizer: Recognizer, engine: Engine):
        self.rec, self.eng = recognizer, engine

    def analyze(self, img: Image.Image, side: str | None = None) -> Hint:
        t0 = time.time()
        r = self.rec.recognize(img)
        if not r.ok:
            return Hint(False, f'识别失败：{r.error}', error=r.error,
                        fen=r.fen_board, elapsed_ms=_ms(t0))

        # 行棋方：默认 = 截图者 = 棋盘下方一方（截图就是为了问自己这步怎么走）
        if side not in ('red', 'black'):
            side = 'black' if r.flipped else 'red'
        fen = f"{r.fen_board} {'w' if side == 'red' else 'b'} - - 0 1"

        a = self.eng.analyze(fen)
        if not a.bestmove:
            msg = '无棋可走（已被将死或困毙）'
            return Hint(False, msg, side=side, fen=fen, error=msg, elapsed_ms=_ms(t0))

        move = uci_to_chinese(r.fen_board, a.bestmove)
        if a.mate is not None:
            score = f'杀{abs(a.mate)}步' if a.mate > 0 else f'被杀{abs(a.mate)}步'
        else:
            score = f'{(a.score_cp or 0) / 100:+.2f}'
        pv = pv_to_chinese(r.fen_board, side == 'red', a.pv)
        text = f"{move}（{score}）"
        if len(pv) > 1:
            text += '  → ' + ' '.join(pv[1:])
        return Hint(True, text, move=move, uci=a.bestmove, side=side, score=score,
                    pv=pv, fen=fen, elapsed_ms=_ms(t0))


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def hint_dict(h: Hint) -> dict:
    return asdict(h)
