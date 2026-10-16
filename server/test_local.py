"""本机自测：不经过 HTTP，直接对一张截图出提示。
用法：python test_local.py 截图路径 [red|black]
"""
import json
import sys
from pathlib import Path

from PIL import Image

from engine import Engine
from recognizer import Recognizer, render_text
from service import HintService

HERE = Path(__file__).parent
CFG = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))

if __name__ == '__main__':
    img = Image.open(sys.argv[1])
    side = sys.argv[2] if len(sys.argv) > 2 else None
    rec = Recognizer(HERE / CFG['model_path'])
    r = rec.recognize(img)
    print('识别：', 'OK' if r.ok else r.error, '| 执黑视角' if r.flipped else '')
    if r.board:
        print(render_text(r.board))
    svc = HintService(rec, Engine(CFG['engine_path'], CFG['threads'], CFG['hash_mb'], CFG['movetime_ms']))
    h = svc.analyze(img, side)
    print('提示：', h.text, f'({h.elapsed_ms}ms)')
    svc.eng.close()
