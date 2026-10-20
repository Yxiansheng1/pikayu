"""皮卡鱼 UCI 封装：常驻子进程，线程安全，固定思考时间出招。"""
from __future__ import annotations

import subprocess
import threading
import queue
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Analysis:
    bestmove: str            # UCI 着法，如 h2e2；无合法着法时为 ''
    score_cp: int | None     # 以行棋方视角的分数（厘兵）
    mate: int | None         # 杀棋步数（正=行棋方将杀对方）
    depth: int
    pv: list[str]


class Engine:
    def __init__(self, path: str | Path, threads: int = 4, hash_mb: int = 256, movetime_ms: int = 1500):
        self.path = str(path)
        self.threads, self.hash_mb, self.movetime_ms = threads, hash_mb, movetime_ms
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.tail: deque[str] = deque(maxlen=5)
        self._start()

    def _start(self):
        # 工作目录设为引擎所在目录，让它自动找到同目录下的 pikafish.nnue
        self.proc = subprocess.Popen(
            [self.path], cwd=str(Path(self.path).parent),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        self.tail.clear()
        self._output = queue.Queue()
        def read_output(proc, output):
            try:
                for line in proc.stdout:
                    output.put(line)
            finally:
                output.put(None)
        threading.Thread(target=read_output, args=(self.proc, self._output), daemon=True).start()
        self._send('uci')
        self._wait_for('uciok')
        self._send(f'setoption name Threads value {self.threads}')
        self._send(f'setoption name Hash value {self.hash_mb}')
        self._send('isready')
        self._wait_for('readyok')

    def _send(self, cmd: str):
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(cmd + '\n')
        self.proc.stdin.flush()

    def _readline(self, timeout: float = 10) -> str:
        assert self.proc and self.proc.stdout
        try:
            line = self._output.get(timeout=max(0, timeout))
        except queue.Empty:
            self.proc.kill()
            self.proc.wait(timeout=2)
            raise TimeoutError('引擎响应超时，下帧将自动重启') from None
        if not line:
            reason = next((l for l in reversed(self.tail) if 'CRITICAL' in l or 'error' in l.lower()), '')
            raise RuntimeError('引擎进程已退出' + (f'：{reason}' if reason else ''))
        line = line.strip()
        self.tail.append(line)
        return line

    def _wait_for(self, token: str, timeout: float = 10) -> list[str]:
        lines = []
        deadline = time.monotonic() + timeout
        while True:
            line = self._readline(deadline - time.monotonic())
            lines.append(line)
            if line.startswith(token):
                return lines

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def analyze(self, fen: str, movetime_ms: int | None = None) -> Analysis:
        with self.lock:
            if not self.alive():
                self._start()   # 崩溃自愈
            self._send(f'position fen {fen}')
            self._send(f'go movetime {movetime_ms or self.movetime_ms}')
            score_cp = mate = None
            depth, pv = 0, []
            for line in self._wait_for('bestmove', (movetime_ms or self.movetime_ms) / 1000 + 10):
                parts = line.split()
                if parts[:1] == ['info'] and 'pv' in parts and 'score' in parts:
                    i = parts.index('score')
                    if parts[i + 1] == 'cp':
                        score_cp, mate = int(parts[i + 2]), None
                    elif parts[i + 1] == 'mate':
                        mate, score_cp = int(parts[i + 2]), None
                    if 'depth' in parts:
                        depth = int(parts[parts.index('depth') + 1])
                    pv = parts[parts.index('pv') + 1:]
                if parts[:1] == ['bestmove']:
                    bm = parts[1] if len(parts) > 1 else ''
                    if bm in ('(none)', '0000'):
                        bm = ''
                    return Analysis(bm, score_cp, mate, depth, pv)
        raise RuntimeError('unreachable')

    def close(self):
        if self.alive():
            try:
                self._send('quit')
                self.proc.wait(timeout=2)
            except Exception:
                self.proc.kill()
