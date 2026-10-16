"""象棋提示服务（P1）。

接口：
  GET  /health                      健康检查
  POST /analyze?side=&format=       上传截图，返回走法提示
        - 图片可以是 multipart 表单字段 file，也可以直接作为请求体（iOS 快捷指令两种都支持）
        - side：red / black，可省略（默认 = 棋盘下方一方，即截图者）
        - format：json（默认）/ text（只返回一行字，给快捷指令通知用）
        - token：如果 config.json 配置了 token，需带上 ?token=xxx
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from PIL import Image

try:  # iPhone 可能发来 HEIC 格式，装了 pillow-heif 就能读
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from engine import Engine
from recognizer import Recognizer
from hint_pipeline import HintPipeline
from service import HintService, hint_dict

HERE = Path(__file__).parent
CFG = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))

app = FastAPI(title='象棋提示服务')
svc = HintService(
    Recognizer(HERE / CFG.get('model_path', 'yolov11.onnx'), threads=CFG.get('recognizer_threads', 2)),
    Engine(CFG['engine_path'], threads=CFG.get('threads', 4), hash_mb=CFG.get('hash_mb', 256),
           movetime_ms=CFG.get('movetime_ms', 1500)),
)
DEBUG_DIR = HERE / 'debug'
LOG_FILE = HERE / 'requests.log'


def log(msg: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


@app.middleware('http')
async def access_log(request: Request, call_next):
    t0 = time.time()
    resp = await call_next(request)
    if request.url.path.startswith(('/frame/latest', '/frame/stats')) or (
            request.url.path == '/frame' and resp.status_code == 200):
        return resp  # 每秒一帧的正常上传和监控轮询不进日志，免得刷屏；失败的仍会记录
    log(f"{request.client.host if request.client else '-'} {request.method} {request.url.path}"
        f"?{request.url.query} -> {resp.status_code} ({int((time.time() - t0) * 1000)}ms)"
        f" ctype={request.headers.get('content-type', '')!r} len={request.headers.get('content-length', '?')}")
    return resp


@app.get('/health')
def health():
    return {'ok': True, 'engine_alive': svc.eng.alive()}


@app.get('/', response_class=HTMLResponse)
def index():
    return TEST_PAGE


# ---------------- P2：屏幕广播帧接收（链路验证） ----------------
class FrameStats:
    def __init__(self):
        self.latest: bytes = b''
        self.count = 0
        self.bytes_total = 0
        self.first_ts = 0.0
        self.last_ts = 0.0
        self.last_seq = 0
        self.mem_mb: list[tuple[float, float]] = []  # (时间, 扩展内存MB)，保留最近 600 个点
        self.mem_peak = 0.0
        self.client_stats = ''
        self.events: list[str] = []

    def snapshot(self) -> dict:
        now = time.time()
        span = (self.last_ts - self.first_ts) if self.count > 1 else 0
        return {
            'count': self.count,
            'fps': round((self.count - 1) / span, 2) if span > 0 else 0,
            'avg_kb': round(self.bytes_total / self.count / 1024, 1) if self.count else 0,
            'last_seq': self.last_seq,
            'since_last_s': round(now - self.last_ts, 1) if self.last_ts else None,
            'mem_now': self.mem_mb[-1][1] if self.mem_mb else None,
            'mem_peak': self.mem_peak,
            'mem_series': [m for _, m in self.mem_mb[-120:]],
            'client_sent_dropped_failed': self.client_stats,
            'events': self.events[-10:],
        }


frames = FrameStats()
pipeline = HintPipeline(svc.rec, svc.eng, log=log)


@app.post('/frame')
async def frame_upload(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(400, 'empty')
    now = time.time()
    if frames.count == 0:
        frames.first_ts = now
    frames.latest = data
    pipeline.submit(data)
    frames.count += 1
    frames.bytes_total += len(data)
    frames.last_ts = now
    frames.last_seq = int(request.headers.get('x-seq', '0') or 0)
    frames.client_stats = request.headers.get('x-stats', '')
    try:
        mem = float(request.headers.get('x-mem-mb', '-1'))
        frames.mem_mb = (frames.mem_mb + [(now, mem)])[-600:]
        frames.mem_peak = max(frames.mem_peak, mem)
    except ValueError:
        pass
    return {'ok': True}


@app.post('/frame/event')
async def frame_event(request: Request):
    ev = request.headers.get('x-event', '?')
    mem = request.headers.get('x-mem-mb', '?')
    line = f"{time.strftime('%H:%M:%S')} {ev} (内存 {mem} MB)"
    frames.events.append(line)
    log(f'  broadcast event: {line}')
    if ev == 'start':  # 新一轮广播，统计清零
        pipeline.reset()
        events = frames.events
        frames.__init__()
        frames.events = events
    return {'ok': True}


@app.get('/frame/latest.jpg')
def frame_latest():
    from fastapi.responses import Response
    if not frames.latest:
        raise HTTPException(404, 'no frame yet')
    return Response(frames.latest, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@app.get('/hint')
def hint():
    """App 画中画轮询：返回最新提示（含屏幕视角棋盘和箭头）。"""
    h = pipeline.snapshot()
    h['recog_ms'] = pipeline.recog_ms
    h['frame_age_s'] = round(time.time() - frames.last_ts, 1) if frames.last_ts else None
    return h


@app.get('/frame/stats')
def frame_stats():
    return frames.snapshot()


@app.get('/frames', response_class=HTMLResponse)
def frames_page():
    return FRAMES_PAGE


FRAMES_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>广播链路监控</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;margin:24px;color:#243142;background:#f3f5f4}
.wrap{display:flex;gap:24px;flex-wrap:wrap}.card{background:#fff;border-radius:12px;padding:16px 20px;border:1px solid #e0e6e3}
img{height:640px;max-width:100%;border-radius:8px;border:1px solid #ddd;background:#222}
table td{padding:4px 12px 4px 0;font-size:15px}td:first-child{color:#66746d}
.big{font-size:28px;font-weight:bold}.ok{color:#236657}.warn{color:#b8860b}.bad{color:#c0392b}
svg{background:#fafbfa;border:1px solid #e5eae7;border-radius:6px}pre{font-size:13px;color:#555}</style></head><body>
<h2>P2 广播链路监控</h2>
<div class="wrap">
<div class="card"><img id="img" alt="等待第一帧…"></div>
<div class="card" style="min-width:340px">
<div>扩展内存（上限约 50MB）</div><div id="mem" class="big">—</div>
<svg id="chart" width="320" height="120"></svg>
<table>
<tr><td>已收帧数</td><td id="count">0</td></tr>
<tr><td>帧率</td><td id="fps">—</td></tr>
<tr><td>平均帧大小</td><td id="kb">—</td></tr>
<tr><td>距上一帧</td><td id="since">—</td></tr>
<tr><td>内存峰值</td><td id="peak">—</td></tr>
<tr><td>手机端 发送/丢弃/失败</td><td id="cs">—</td></tr>
</table>
<div style="margin-top:10px">当前提示</div><div id="hint" class="big ok" style="font-size:22px">—</div><pre id="hintmeta"></pre>
<div style="margin-top:10px">广播事件</div><pre id="ev"></pre>
</div></div>
<script>
async function tick(){
  try{
    const s=await (await fetch('/frame/stats')).json();
    const set=(id,v)=>document.getElementById(id).textContent=v;
    set('count',s.count);set('fps',s.fps+' fps');set('kb',s.avg_kb+' KB');
    set('since',s.since_last_s==null?'—':s.since_last_s+' 秒');
    set('peak',s.mem_peak? s.mem_peak.toFixed(1)+' MB':'—');set('cs',s.client_sent_dropped_failed||'—');
    set('ev',(s.events||[]).join('\\n'));
    const m=document.getElementById('mem');
    if(s.mem_now!=null){m.textContent=s.mem_now.toFixed(1)+' MB';
      m.className='big '+(s.mem_now<35?'ok':s.mem_now<45?'warn':'bad');}
    const pts=s.mem_series||[];const svg=document.getElementById('chart');
    let h='<line x1="0" y1="'+(120-50*2)+'" x2="320" y2="'+(120-50*2)+'" stroke="#c0392b" stroke-dasharray="4"/>';
    if(pts.length>1){h+='<polyline fill="none" stroke="#236657" stroke-width="2" points="'+
      pts.map((v,i)=>(i*320/(pts.length-1)).toFixed(1)+','+(120-Math.min(v,60)*2).toFixed(1)).join(' ')+'"/>';}
    svg.innerHTML=h;
    if(s.count>0) document.getElementById('img').src='/frame/latest.jpg?t='+Date.now();
    const h=await (await fetch('/hint')).json();
    set('hint',h.text||'—');
    set('hintmeta','状态 '+h.status+(h.side_to_move?' · 轮到'+(h.side_to_move==='red'?'红':'黑'):'')+
      (h.latency_ms?' · 提示耗时 '+h.latency_ms+'ms':'')+' · 识别 '+h.recog_ms+'ms'+(h.pv&&h.pv.length>1?'\n变化: '+h.pv.join(' '):''));
  }catch(e){}
}
setInterval(tick,1000);tick();
</script></body></html>"""


TEST_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>象棋提示服务</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;max-width:640px;margin:32px auto;padding:0 16px;color:#243142}
h1{font-size:22px}select,button,input{font-size:16px;margin:6px 0}button{padding:8px 20px;background:#236657;color:#fff;border:0;border-radius:6px;cursor:pointer}
#hint{font-size:26px;font-weight:bold;color:#236657;margin:18px 0 6px}#meta{color:#66746d;font-size:14px}
pre{background:#f3f5f4;padding:12px;border-radius:6px;font-size:13px;white-space:pre-wrap;word-break:break-all}
img{max-width:220px;border:1px solid #ddd;border-radius:6px;margin-top:10px}</style></head><body>
<h1>象棋提示服务 · 测试页</h1>
<p>选一张 JJ象棋 对局截图，点"分析"。</p>
<input type="file" id="f" accept="image/*"><br>
<label>行棋方：<select id="side"><option value="">自动（截图下方一方）</option>
<option value="red">红方</option><option value="black">黑方</option></select></label><br>
<button onclick="go()">分析</button>
<div id="hint"></div><div id="meta"></div><img id="pv" hidden><pre id="raw" hidden></pre>
<script>
async function go(){
  const file=document.getElementById('f').files[0];
  if(!file){alert('先选一张截图');return}
  const pv=document.getElementById('pv');pv.src=URL.createObjectURL(file);pv.hidden=false;
  document.getElementById('hint').textContent='分析中…';document.getElementById('meta').textContent='';
  const side=document.getElementById('side').value;
  const fd=new FormData();fd.append('file',file);
  try{
    const r=await fetch('/analyze'+(side?'?side='+side:''),{method:'POST',body:fd});
    const j=await r.json();
    document.getElementById('hint').textContent=j.text;
    document.getElementById('meta').textContent=j.ok?('执'+(j.side==='red'?'红':'黑')+' · 耗时 '+j.elapsed_ms+' ms'):'';
    const raw=document.getElementById('raw');raw.textContent=JSON.stringify(j,null,2);raw.hidden=false;
  }catch(e){document.getElementById('hint').textContent='请求失败：'+e}
}
</script></body></html>"""


@app.post('/analyze')
async def analyze(request: Request,
                  side: str | None = Query(None),
                  format: str = Query('json'),
                  token: str | None = Query(None)):
    if CFG.get('token') and token != CFG['token']:
        raise HTTPException(401, 'token 错误')

    data = await _read_image_bytes(request)
    log(f'  analyze: 收到 {len(data)} 字节, 文件头={data[:12]!r}')
    if not data:
        return _reply(format, ok=False, text='没有收到图片', status=400)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        log(f'  analyze: 图片 {img.format} {img.size} {img.mode}')
    except Exception as e:
        log(f'  analyze: 图片解析失败 {e!r}')
        return _reply(format, ok=False, text='图片格式无法识别（请用 PNG/JPEG）', status=400)

    if CFG.get('save_debug'):
        DEBUG_DIR.mkdir(exist_ok=True)
        img.convert('RGB').save(DEBUG_DIR / f'{int(time.time() * 1000)}.jpg', quality=85)

    try:
        h = svc.analyze(img, side)
    except Exception as e:
        log(f'  analyze: 处理异常 {e!r}')
        return _reply(format, ok=False, text=f'服务内部错误：{e}', status=500)
    log(f'  analyze: 返回 {h.text!r}')
    if format == 'text':
        return PlainTextResponse(h.text)
    return JSONResponse(hint_dict(h))


async def _read_image_bytes(request: Request) -> bytes:
    ctype = request.headers.get('content-type', '')
    if ctype.startswith('multipart/form-data'):
        form = await request.form()
        for v in form.values():
            if hasattr(v, 'read'):
                return await v.read()
        return b''
    return await request.body()


def _reply(format: str, ok: bool, text: str, status: int = 200):
    if format == 'text':
        return PlainTextResponse(text, status_code=status)
    return JSONResponse({'ok': ok, 'text': text, 'error': text}, status_code=status)


@app.on_event('shutdown')
def _shutdown():
    svc.eng.close()


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=CFG.get('host', '0.0.0.0'), port=CFG.get('port', 8765))
