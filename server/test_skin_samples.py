"""测试用户提供的皮肤预览截图。用法：python test_skin_samples.py 图片目录

这里只评价截图识别，不调用引擎、不把预览图当作真实对局。
720/JPEG60 使用 Pillow 近似采集压缩，不等同于 iOS 编码器。
"""
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image
from recognizer import Recognizer

SAMPLES = [
    ('a6c74e6f9b7807402b5a9dfb96b3c21b.jpg', '枫桥夜战（图1）'),
    ('606072423295a1f7cfe97732be6c1ff4.jpg', '15周年庆'),
    ('3e65a12d95257e1bd64c3cbd2451bf27.jpg', '云开月明'),
    ('0284c87e7f93c0aafc1424943b024b55.jpg', '千祥云集'),
    ('64269eab403bf24dea945d57d63c0864.jpg', '枫桥夜战（图5）'),
    ('233943af992f21e419824ebe2a0d5dae.jpg', '引龙出水'),
    ('820498614b4e416a28cf52ba2964feb5.jpg', '擎天'),
    ('f63fbf7a7653d8e0c8b175d1c821fa7d.jpg', '鹤舞云山'),
    ('c5990f8f010d2dcb0493a550dc8e8ff1.jpg', '跃浪端阳'),
    ('ca84d06385f8c8d07d60b9fee52ba643.jpg', '春江花月夜'),
]
# 人工对照用户截图：行列从0起，红方在下，五枚棋子及其他空格全部比对。
EXPECTED = {(1, 3): 'k', (2, 4): 'b', (7, 3): 'A', (7, 4): 'K', (9, 4): 'C'}


def run(directory):
    recognizer = Recognizer(Path(__file__).with_name('yolov11.onnx'))
    rows = []
    for filename, name in SAMPLES:
        with Image.open(directory / filename) as source:
            original = source.convert('RGB')
        variants = [('原图', original)]
        for width, quality in [(720, 60), (960, 80)]:
            reduced = original.resize((width, round(original.height * width / original.width)), Image.Resampling.LANCZOS)
            jpeg = io.BytesIO()
            reduced.save(jpeg, 'JPEG', quality=quality)
            jpeg.seek(0)
            with Image.open(jpeg) as compressed:
                variants.append((f'{width}宽JPEG{quality}近似采集', compressed.convert('RGB')))
        for variant, image in variants:
            start = time.perf_counter()
            result = recognizer.recognize(image)
            found = {(r, c): p for r, row in enumerate(result.board) for c, p in enumerate(row) if p != ' '}
            differences = [
                {'row': r, 'col': c, 'expected': EXPECTED.get((r, c), ' '), 'actual': found.get((r, c), ' ')}
                for r, c in sorted(set(EXPECTED) | set(found))
                if EXPECTED.get((r, c), ' ') != found.get((r, c), ' ')
            ]
            row = dict(file=filename, skin=name, variant=variant, exact=found == EXPECTED,
                       accepted=result.ok, error=result.error, differences=differences,
                       fen=result.fen_board, board_box=result.board_box,
                       elapsed_ms=round((time.perf_counter() - start) * 1000))
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False))
    output = Path(__file__).parent / 'debug' / 'skin_test_results.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'expected': [{'row': r, 'col': c, 'piece': p} for (r, c), p in EXPECTED.items()],
                                  'results': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Exact: {sum(r["exact"] for r in rows)}/{len(rows)}; results: {output}')


if __name__ == '__main__':
    run(Path(sys.argv[1]))
