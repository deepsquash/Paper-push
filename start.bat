@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

python -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo 首次运行，安装依赖...
    python -m pip install -r requirements.txt
)

echo 启动 PaperPush 网页版，浏览器将自动打开 http://localhost:8080
echo 关闭本窗口即停止服务。
start "" http://localhost:8080
python app.py --port 8080
pause
