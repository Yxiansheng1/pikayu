@echo off
chcp 65001 >nul
cd /d %~dp0

if not exist .venv (
    echo [1/2] 首次运行，创建虚拟环境并安装依赖...
    python -m venv .venv || goto :err
    .venv\Scripts\python -m pip install -r requirements.txt || goto :err
)

echo [2/2] 启动象棋提示服务，端口 8765 ...
.venv\Scripts\python app.py
pause
exit /b

:err
echo 安装失败，请检查是否已安装 Python 3.10+ 并加入 PATH
pause
