"""P3：帧流 -> 自动识别 -> 稳定局面判定 -> 判断行棋方 -> 引擎提示。

后台单线程处理"最新一帧"（处理不过来就跳帧，绝不积压）：
  1. 识别：帧 -> 棋盘（统一成红方在下的坐标）
  2. 稳定：连续 STABLE_FRAMES 帧识别结果相同才算一个"稳定局面"，过滤走子动画和弹窗
  3. 行棋方：对比上一个稳定局面，刚走动的是哪一方 -> 轮到另一方
  4. 轮到用户（棋盘下方一方）时才算提示；轮到对方时状态为 opponent_turn
"""
from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field, asdict

from PIL import Image

from engine import Engine
from notation import uci_to_chinese, pv_to_chinese
from recognizer import Recognizer
from rules import resolve_side

STABLE_FRAMES = 2
BAD_FRAME_DIR = 'debug/bad_frames'
START_BOARD = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'


@dataclass
class Hint:
    seq: int = 0                      # 每次内容变化 +1，App 据此判断是否重绘
    status: str = 'waiting'           # waiting / thinking / your_turn / opponent_turn / no_board
    text: str = '等待画面…'
    move: str = ''
    score: str = ''
    pv: list = field(default_factory=list)
    user_side: str = ''               # red / black：棋盘下方一方
    side_to_move: str = ''
    board: list = field(default_factory=list)   # 10 行字符串，按手机屏幕视角（上方一行在前），'.' 为空
    arrow: list = field(default_factory=list)   # [from_row, from_col, to_row, to_col]，屏幕视角
    updated: float = 0.0
    latency_ms: int = 0               # 从稳定帧到达到提示算出的耗时


class HintPipeline:
    def __init__(self, recognizer: Recognizer, engine: Engine, log=print):
        self.rec, self.eng, self.log = recognizer, engine, log
        self.hint = Hint()
        self._lock = threading.Lock()
        self._frame: bytes | None = None
        self._frame_ts = 0.0
        self._event = threading.Event()
        self._candidate: str | None = None
        self._candidate_count = 0
        self._stable: list[list[str]] | None = None
        self._stable_key = ''
        self.frames_processed = 0
        self.recog_ms = 0
        self._last_bad = ''
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- 对外 ----------
    def submit(self, jpeg: bytes):
        with self._lock:
            self._frame, self._frame_ts = jpeg, time.time()
        self._event.set()

    def reset(self):
        with self._lock:
            self._candidate, self._candidate_count = None, 0
            self._stable, self._stable_key = None, ''
            self._set(status='waiting', text='等待画面…', move='', score='', pv=[], board=[], arrow=[])

    def snapshot(self) -> dict:
        with self._lock:
            return asdict(self.hint)

    # ---------- 后台循环 ----------
    def _loop(self):
        while True:
            self._event.wait()
            self._event.clear()
            with self._lock:
                data, ts = self._frame, self._frame_ts
                self._frame = None
            if not data:
                continue
            try:
                self._process(data, ts)
            except Exception as e:  # 单帧出错不影响后续
                self.log(f'  hint pipeline 异常: {e!r}')

    def _process(self, data: bytes, frame_ts: float):
        t0 = time.time()
        img = Image.open(io.BytesIO(data))
        r = self.rec.recognize(img)
        self.recog_ms = int((time.time() - t0) * 1000)
        self.frames_processed += 1

        if not r.ok:
            # 识别失败（不是棋盘 / 动画中 / 弹窗遮挡）：清空候选，保留上一个提示不动
            self._candidate, self._candidate_count = None, 0
            if self._stable is None:
                with self._lock:
                    self._set(status='no_board', text='未检测到棋盘')
            return

        key = r.fen_board
        if key == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate, self._candidate_count = key, 1
        if self._candidate_count < STABLE_FRAMES or key == self._stable_key:
            return

        # ---- 新的稳定局面 ----
        user_side = 'black' if r.flipped else 'red'
        prev = self._stable
        guess = self._infer_side_to_move(prev, r.board, key, user_side)
        # 用规则校验兜底：不合法的局面不送引擎（Pikafish 会直接退出）；与"将军"矛盾时纠正行棋方
        side_to_move, why = resolve_side(r.board, guess)
        if side_to_move is None:
            if key != self._last_bad:
                self._last_bad = key
                self.log(f'  hint: 局面不合法，跳过（{why}） fen={key}')
                self._save_bad_frame(data, why)
            return
        if why:
            self.log(f'  hint: {why}')
        self._stable, self._stable_key = r.board, key
        view_board = _to_view(r.board, r.flipped)

        if side_to_move != user_side:
            with self._lock:
                self._set(status='opponent_turn', text='对方思考中…', move='', score='', pv=[], arrow=[],
                          user_side=user_side, side_to_move=side_to_move, board=view_board)
            return

        with self._lock:
            self._set(status='thinking', text='计算中…', move='', score='', pv=[], arrow=[],
                      user_side=user_side, side_to_move=side_to_move, board=view_board)

        fen = f"{key} {'w' if side_to_move == 'red' else 'b'} - - 0 1"
        a = self.eng.analyze(fen)
        if key != self._stable_key:  # 算的过程中局面已经变了，丢弃
            return
        if not a.bestmove:
            with self._lock:
                self._set(status='your_turn', text='无棋可走', move='', arrow=[])
            return

        move = uci_to_chinese(key, a.bestmove)
        if a.mate is not None:
            score = f'杀{abs(a.mate)}步' if a.mate > 0 else f'被杀{abs(a.mate)}步'
        else:
            score = f'{(a.score_cp or 0) / 100:+.2f}'
        pv = pv_to_chinese(key, side_to_move == 'red', a.pv)
        arrow = _uci_to_view_arrow(a.bestmove, r.flipped)
        with self._lock:
            self._set(status='your_turn', text=f'{move}（{score}）', move=move, score=score, pv=pv,
                      arrow=arrow, latency_ms=int((time.time() - frame_ts) * 1000))
        self.log(f'  hint: {move}（{score}） 耗时 {int((time.time() - frame_ts) * 1000)}ms')

    def _save_bad_frame(self, data: bytes, why: str):
        import os
        try:
            os.makedirs(BAD_FRAME_DIR, exist_ok=True)
            files = sorted(os.listdir(BAD_FRAME_DIR))
            for f in files[:-19]:  # 最多保留 20 张
                os.remove(os.path.join(BAD_FRAME_DIR, f))
            with open(os.path.join(BAD_FRAME_DIR, f'{int(time.time() * 1000)}.jpg'), 'wb') as fp:
                fp.write(data)
        except Exception:
            pass

    @staticmethod
    def _infer_side_to_move(prev, cur, key, user_side) -> str:
        if key == START_BOARD:
            return 'red'
        if prev is None:
            return user_side  # 第一次看到局面：默认用户在思考
        movers = set()
        for i in range(10):
            for j in range(9):
                a, b = prev[i][j], cur[i][j]
                if b != ' ' and a != b:          # 这个格子出现了新的棋子 = 落点
                    movers.add('red' if b.isupper() else 'black')
        if len(movers) == 1:
            mover = movers.pop()
            return 'black' if mover == 'red' else 'red'
        return user_side  # 变化太多（漏帧 / 悔棋 / 新开局）：默认轮到用户

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self.hint, k, v)
        self.hint.seq += 1
        self.hint.updated = time.time()


def _to_view(grid, flipped) -> list[str]:
    """红方在下的棋盘 -> 手机屏幕上看到的样子（执黑时旋转 180°）。"""
    g = [row[::-1] for row in grid[::-1]] if flipped else grid
    return [''.join(c if c != ' ' else '.' for c in row) for row in g]


def _uci_to_view_arrow(uci: str, flipped: bool) -> list[int]:
    def sq(s):
        col, rank = ord(s[0]) - ord('a'), int(s[1:])
        row = 9 - rank
        return (9 - row, 8 - col) if flipped else (row, col)
    (r0, c0), (r1, c1) = sq(uci[:2]), sq(uci[2:])
    return [r0, c0, r1, c1]
