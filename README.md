# pikayu · 随时随地象棋提示

个人练习用的中国象棋提示工具：在 iPhone 上下 JJ象棋 时，轻点手机背面两下，家里电脑识别棋盘、用皮卡鱼（Pikafish）计算，几秒内把建议着法推回手机。

```
iPhone 快捷指令 ──截屏 POST──▶ Tailscale ──▶ 家里电脑 FastAPI
   ▲                                         │ YOLO 识别棋盘 → FEN
   └──────── "炮二平五（+0.35） → …" ◀────────┘ Pikafish 计算 → 中文记法
```

## 目录

| 路径 | 说明 |
|---|---|
| `server/app.py` | HTTP 服务：`/analyze` 收截图返回提示，`/` 自带测试页，`/health` 健康检查 |
| `server/recognizer.py` | 棋盘识别（YOLOv11 ONNX），截图 → 10×9 棋盘 → FEN，自动判断红/黑视角 |
| `server/engine.py` | Pikafish UCI 常驻进程封装，崩溃自愈 |
| `server/notation.py` | UCI 着法 → 中文记法（炮二平五 / 马２进３ / 前炮平五） |
| `server/service.py` | 识别 + 引擎 + 记法的核心流程，与 Web 框架无关 |
| `server/config.json` | 引擎路径、线程、思考时间、端口、token 等 |
| `docs/` | 需求文档（v1 投屏方案、v2 随时随地方案） |

## 运行（Windows）

1. 下载 [Pikafish](https://github.com/official-pikafish/Pikafish/releases)，解压后在 `server/config.json` 里填好 `engine_path`（`pikafish.nnue` 需与引擎 exe 同目录）。
2. 双击 `server/start.bat`：首次会创建虚拟环境并安装依赖，之后启动服务（默认端口 8765）。
3. 浏览器打开 `http://localhost:8765/`，上传一张对局截图验证。

> 若用 Anaconda 的 Python 建虚拟环境，onnxruntime 需锁定 1.18.1（已在 requirements.txt 中锁定），更新版本会因旧 VC++ 运行库在 import 时崩溃。

## 手机端

1. 电脑和 iPhone 装 Tailscale 并登录同一账号。
2. iPhone 快捷指令：**截屏** → **获取 URL 内容**（`http://<电脑 Tailscale IP>:8765/analyze?format=text`，方法 POST，请求体"文件"= 截屏）→ **显示结果**。
3. 设置 → 辅助功能 → 触控 → 轻点背面 → 轻点两下 → 选该快捷指令。

## 接口

`POST /analyze?side=red|black&format=json|text`

- 图片：multipart 字段或原始请求体均可，支持 PNG / JPEG / HEIC
- `side` 省略时默认为棋盘下方一方（截图者）
- `format=text` 只返回一行提示，供快捷指令直接显示

## 路线

- **P1** 服务端 + 快捷指令截图版 ✅
- **P2** iOS 链路验证 App：ReplayKit 屏幕广播采集回传（验证 50MB 内存限制）
- **P3** 画中画（PiP）悬浮实时提示

## 致谢与许可

棋盘识别模型 `server/yolov11.onnx` 及推理逻辑来自 [TCHESS (sojourners/public-Xiangqi)](https://github.com/sojourners/public-Xiangqi)，GPL-3.0 许可；引擎为 [Pikafish](https://github.com/official-pikafish/Pikafish)，GPL-3.0 许可。

本项目以 [GPL-3.0](LICENSE) 许可发布。
