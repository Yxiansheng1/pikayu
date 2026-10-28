import io
import time
import unittest
from unittest.mock import Mock, patch
from PIL import Image
from engine import Analysis
from hint_pipeline import HintPipeline, START_BOARD
from recognizer import Result, _to_fen


def start_board():
    board = []
    for row in START_BOARD.split('/'):
        cells = []
        for c in row:
            cells.extend([' '] * int(c) if c.isdigit() else [c])
        board.append(cells)
    return board


def result(board):
    return Result(ok=True, board=board, fen_board=_to_fen(board))


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        with patch('hint_pipeline.threading.Thread'):
            self.p = HintPipeline(Mock(), Mock(), log=lambda _: None)
        self.p.eng.movetime_ms = 1500
        self.p.eng.analyze.return_value = Analysis('h2e2', 10, None, 15, ['h2e2'])
        data = io.BytesIO()
        Image.new('RGB', (20, 20)).save(data, 'JPEG')
        self.data = data.getvalue()
        # 另一帧必须有不同字节：同一份 JPEG 必然识别出同一个局面
        other = io.BytesIO()
        Image.new('RGB', (20, 20), (200, 30, 30)).save(other, 'JPEG')
        self.data2 = other.getvalue()

    def process(self):
        self.p._process(self.data, time.time())

    def test_middle_position_does_not_guess_turn(self):
        board = start_board()
        board[6][0], board[5][0] = ' ', 'P'
        self.p.rec.recognize.return_value = result(board)
        self.process(); self.process()
        self.p.eng.analyze.assert_not_called()
        self.assertEqual(self.p.snapshot()['side_to_move'], '')

    def test_first_changed_frame_hides_old_move(self):
        self.p.hint.move, self.p.hint.arrow = 'old', [1, 1, 2, 2]
        self.p.rec.recognize.return_value = result(start_board())
        self.process()
        self.assertEqual(self.p.snapshot()['arrow'], [])
        self.assertEqual(self.p.snapshot()['move'], '')

    def test_new_frame_during_search_discards_move(self):
        board = start_board()
        moved = [row[:] for row in board]
        moved[6][0], moved[5][0] = ' ', 'P'
        # 第 1 次识别给首帧，第 2 次给搜索期间到达的新帧（不同字节）。
        # 中间那一帧走逐字节缓存，不会再触发一次推理。
        self.p.rec.recognize.side_effect = [result(board), result(moved)]
        def analyze(_):
            self.p.submit(self.data2)
            return Analysis('h2e2', 10, None, 15, ['h2e2'])
        self.p.eng.analyze.side_effect = analyze
        self.process(); self.process()
        self.assertEqual(self.p.snapshot()['arrow'], [])
        self.assertEqual(self.p.snapshot()['status'], 'waiting')

    def test_same_frame_during_search_can_publish(self):
        self.p.rec.recognize.return_value = result(start_board())
        self.p.submit(self.data)
        self.process(); self.process()
        self.assertEqual(self.p.snapshot()['status'], 'your_turn')
        self.assertTrue(self.p.snapshot()['arrow'])

    def test_recognition_cached_per_frame_bytes(self):
        """同一帧字节只推理一次；换帧必须重新推理。"""
        self.p.rec.recognize.return_value = result(start_board())
        self.p._recognize(self.data)
        self.p._recognize(self.data)
        self.assertEqual(self.p.rec.recognize.call_count, 1)
        self.p._recognize(self.data2)
        self.assertEqual(self.p.rec.recognize.call_count, 2)

    def test_reset_during_search_discards_result(self):
        self.p.rec.recognize.return_value = result(start_board())
        def analyze(_):
            self.p.reset()
            return Analysis('h2e2', 10, None, 15, ['h2e2'])
        self.p.eng.analyze.side_effect = analyze
        self.process(); self.process()
        self.assertEqual(self.p.snapshot()['status'], 'waiting')
        self.assertEqual(self.p.snapshot()['arrow'], [])

    def test_single_square_misread_does_not_infer_turn(self):
        board = start_board()
        wrong = [row[:] for row in board]
        wrong[9][1] = 'C'
        self.assertIsNone(self.p._infer_side_to_move(board, wrong, _to_fen(wrong), 'red'))

    def test_single_move_infers_opposite_side(self):
        board = start_board()
        moved = [row[:] for row in board]
        moved[6][0], moved[5][0] = ' ', 'P'
        self.assertEqual(self.p._infer_side_to_move(board, moved, _to_fen(moved), 'red'), 'black')


if __name__ == '__main__':
    unittest.main()
