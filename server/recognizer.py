"""棋盘识别：截图 -> 10x9 棋盘 -> FEN。

推理流程按 TCHESS (sojourners/public-Xiangqi, GPL-3.0) 的 Yolo11Model 复刻：
  - 输入 640x640 letterbox，等比缩放后居中，四周填充 114 灰
  - 输出 [1, 4+15, 8400]，前 4 维为中心点 xywh，后 15 维为类别分数
  - 类别 14 种棋子 + '0'(棋盘框)；先取最大棋盘框，再把棋子中心映射到 9x10 格点
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

SIZE = 640
CONF = 0.5
IOU = 0.45
# 小写=黑方，大写=红方，'0'=棋盘框（与 TCHESS OnnxModel.labels 顺序一致）
LABELS = ['n', 'b', 'a', 'k', 'r', 'c', 'p', 'R', 'N', 'A', 'K', 'B', 'C', 'P', '0']


@dataclass
class Det:
    label: str
    cx: float
    cy: float
    w: float
    h: float
    conf: float


@dataclass
class Result:
    ok: bool
    board: list[list[str]] = field(default_factory=list)  # 10 行 x 9 列，' ' 为空
    fen_board: str = ''           # 仅棋盘部分，已统一成红方在下
    flipped: bool = False         # 截图里黑方在下（执黑视角）
    board_box: tuple | None = None  # (x0, y0, x1, y1) 原图坐标，第一/最后一条线
    dets: list[Det] = field(default_factory=list)
    error: str = ''


class Recognizer:
    def __init__(self, model_path: str | Path, threads: int = 2):
        opt = ort.SessionOptions()
        opt.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(str(model_path), opt, providers=['CPUExecutionProvider'])
        self.input_name = self.sess.get_inputs()[0].name

    # ---------- 推理 ----------
    def _preprocess(self, img: Image.Image):
        img = img.convert('RGB')
        rate = SIZE / max(img.width, img.height)
        w, h = round(img.width * rate), round(img.height * rate)
        resized = img.resize((w, h), Image.BILINEAR)
        canvas = np.full((SIZE, SIZE, 3), 114, dtype=np.uint8)
        left, top = (SIZE - w) // 2, (SIZE - h) // 2
        canvas[top:top + h, left:left + w] = np.asarray(resized)
        x = canvas.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        # 与 Java 一致：用浮点 padding 反算坐标
        pad_x = (SIZE - img.width * rate) / 2
        pad_y = (SIZE - img.height * rate) / 2
        return x, rate, pad_x, pad_y

    def detect(self, img: Image.Image) -> list[Det]:
        x, rate, pad_x, pad_y = self._preprocess(img)
        out = self.sess.run(None, {self.input_name: x})[0][0]  # (19, 8400)
        boxes, scores = out[:4].T, out[4:].T
        cls = scores.argmax(1)
        conf = scores.max(1)
        keep = conf > CONF
        dets = []
        for (cx, cy, w, h), c, s in zip(boxes[keep], cls[keep], conf[keep]):
            dets.append(Det(LABELS[c], (cx - pad_x) / rate, (cy - pad_y) / rate, w / rate, h / rate, float(s)))
        return _nms_per_class(dets)

    # ---------- 解析成棋盘 ----------
    def recognize(self, img: Image.Image) -> Result:
        dets = self.detect(img)
        boards = [d for d in dets if d.label == '0']
        if not boards:
            return Result(ok=False, dets=dets, error='未检测到棋盘（可能不是对局画面）')
        b = max(boards, key=lambda d: d.w * d.h)
        x0, y0 = b.cx - b.w / 2, b.cy - b.h / 2
        pw, ph = b.w / 8, b.h / 9

        grid = [[' '] * 9 for _ in range(10)]
        conf_grid = [[0.0] * 9 for _ in range(10)]
        for d in dets:
            if d.label == '0':
                continue
            # 尺寸过滤：真棋子宽约 0.9~1.2 格；画中画悬浮窗里的小棋盘也会被屏幕采集录进来，
            # 它的棋子不到 0.3 格，按 0.55 格为界丢掉，避免混进识别结果
            if d.w < 0.55 * pw or d.h < 0.55 * ph:
                continue
            j = int((d.cx - (x0 - pw / 2)) // pw)
            i = int((d.cy - (y0 - ph / 2)) // ph)
            if 0 <= i <= 9 and 0 <= j <= 8 and d.conf > conf_grid[i][j]:
                grid[i][j] = d.label
                conf_grid[i][j] = d.conf

        # 视角判断：红帅在下半盘 = 正常；否则旋转 180°
        flipped = False
        for i in range(10):
            for j in range(9):
                if grid[i][j] == 'K':
                    flipped = i < 5
        if flipped:
            grid = [row[::-1] for row in grid[::-1]]

        res = Result(ok=True, board=grid, fen_board=_to_fen(grid), flipped=flipped,
                     board_box=(x0, y0, x0 + b.w, y0 + b.h), dets=dets)
        err = validate(grid)
        if err:
            res.ok, res.error = False, err
        return res


def _iou(a: Det, b: Det) -> float:
    ax0, ay0, ax1, ay1 = a.cx - a.w / 2, a.cy - a.h / 2, a.cx + a.w / 2, a.cy + a.h / 2
    bx0, by0, bx1, by1 = b.cx - b.w / 2, b.cy - b.h / 2, b.cx + b.w / 2, b.cy + b.h / 2
    iw, ih = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / (a.w * a.h + b.w * b.h - inter)


def _nms_per_class(dets: list[Det]) -> list[Det]:
    out = []
    for lab in LABELS:
        cand = sorted((d for d in dets if d.label == lab), key=lambda d: -d.conf)
        while cand:
            best = cand.pop(0)
            out.append(best)
            cand = [d for d in cand if _iou(best, d) < IOU]
    return out


def _to_fen(grid) -> str:
    rows = []
    for row in grid:
        s, n = '', 0
        for c in row:
            if c == ' ':
                n += 1
            else:
                s += (str(n) if n else '') + c
                n = 0
        rows.append(s + (str(n) if n else ''))
    return '/'.join(rows)


LIMITS = {'k': 1, 'a': 2, 'b': 2, 'n': 2, 'r': 2, 'c': 2, 'p': 5}


def validate(grid) -> str:
    """基础合法性检查；返回错误描述，合法返回空串。"""
    cnt: dict[str, int] = {}
    for row in grid:
        for c in row:
            if c != ' ':
                cnt[c] = cnt.get(c, 0) + 1
    if cnt.get('K', 0) != 1 or cnt.get('k', 0) != 1:
        return f'将帅数量异常（红帅{cnt.get("K", 0)} 黑将{cnt.get("k", 0)}）'
    for c, n in cnt.items():
        if n > LIMITS[c.lower()]:
            return f'棋子数量超限：{c} x{n}'
    return ''


def render_text(grid) -> str:
    names = {'K': '帅', 'A': '仕', 'B': '相', 'N': '马', 'R': '车', 'C': '炮', 'P': '兵',
             'k': '将', 'a': '士', 'b': '象', 'n': '馬', 'r': '車', 'c': '砲', 'p': '卒', ' ': '・'}
    return '\n'.join(' '.join(names[c] for c in row) for row in grid)
