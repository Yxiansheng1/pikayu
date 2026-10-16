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
