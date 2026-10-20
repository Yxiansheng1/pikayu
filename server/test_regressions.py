"""运行：.venv\\Scripts\\python.exe -m unittest test_regressions -v"""
import ast
import io
import queue
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from engine import Engine
from hint_pipeline import HintPipeline
from recognizer import Recognizer, Result


class RegressionTests(unittest.TestCase):
    def test_gray_retry_rejects_moved_piece(self):
        rec = Recognizer.__new__(Recognizer)
        original = [[' '] * 9 for _ in range(10)]
        original[7][4], original[9][4] = 'K', 'C'
        moved = [row[:] for row in original]
        moved[9][4], moved[8][4] = ' ', 'C'
        primary = Result(ok=False, board=original, error='uncertain')
        rec._recognize_once = Mock(side_effect=[primary, Result(ok=True, board=moved)])
        self.assertIs(rec.recognize(Image.new('RGB', (100, 100))), primary)

    def test_gray_retry_preserves_color(self):
        rec = Recognizer.__new__(Recognizer)
        original = [[' '] * 9 for _ in range(10)]
        original[7][3] = 'C'
        alternate = [row[:] for row in original]
        alternate[7][3] = 'a'
        primary = Result(ok=False, board=original)
        rec._recognize_once = Mock(side_effect=[primary, Result(ok=True, board=alternate)])
        self.assertIs(rec.recognize(Image.new('RGB', (100, 100))), primary)

    def test_gray_retry_accepts_same_positions_and_colors(self):
        rec = Recognizer.__new__(Recognizer)
        original = [[' '] * 9 for _ in range(10)]
        original[7][3] = 'C'
        corrected = [row[:] for row in original]
        corrected[7][3] = 'A'
        alternate = Result(ok=True, board=corrected)
        rec._recognize_once = Mock(side_effect=[Result(ok=False, board=original), alternate])
        self.assertIs(rec.recognize(Image.new('RGB', (100, 100))), alternate)

    def test_saved_advisor_image(self):
        sample = Path(__file__).parent / 'debug/bad_frames/1792466476954.jpg'
        if not sample.exists():
            self.skipTest('本地错误样本不存在')
        rec = Recognizer(Path(__file__).parent / 'yolov11.onnx')
        with Image.open(sample) as img:
            result = rec.recognize(img)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.board[9][3], 'A')  # 仕，不是相
        self.assertGreater(result.board_box[1], 300)  # 仍是原图坐标

    def test_invalid_frame_clears_old_arrow(self):
        with patch('hint_pipeline.threading.Thread'):
            p = HintPipeline(Mock(), Mock())
        p.rec.recognize.return_value = Result(ok=False, error='棋盘遮挡')
        p._stable = [[' '] * 9 for _ in range(10)]
        p._stable_key = 'old'
        p.hint.arrow = [0, 0, 1, 0]
        data = io.BytesIO()
        Image.new('RGB', (10, 10)).save(data, 'JPEG')
        p._process(data.getvalue(), 0)
        self.assertEqual(p.snapshot()['status'], 'no_board')
        self.assertEqual(p.snapshot()['arrow'], [])
        self.assertEqual(p._stable_key, '')

    def test_engine_silence_has_deadline(self):
        engine = Engine.__new__(Engine)
        engine.proc = Mock()
        engine._output = queue.Queue()
        with self.assertRaises(TimeoutError):
            engine._wait_for('bestmove', timeout=0.02)
        engine.proc.kill.assert_called_once()

    def test_monitor_javascript_parses(self):
        if not shutil.which('node'):
            self.skipTest('Node 未安装')
        tree = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        scripts = []
        for item in ast.walk(tree):
            if isinstance(item, ast.Constant) and isinstance(item.value, str) and '<script>' in item.value:
                scripts.append(item.value.split('<script>', 1)[1].split('</script>', 1)[0])
        self.assertTrue(scripts)
        with tempfile.TemporaryDirectory() as directory:
            for i, script in enumerate(scripts):
                file = Path(directory) / f'monitor{i}.js'
                file.write_text(script, encoding='utf-8')
                result = subprocess.run(['node', '--check', str(file)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
