@echo off
chcp 65001 >nul
setlocal
title GroupBrief
cd /d "%~dp0"

echo ============================================
echo   GroupBrief v1.0.0
echo ============================================

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python。请先安装 Python 3.10 或更高版本。
    exit /b 1
)

if not exist .venv\Scripts\python.exe (
    echo [1/4] 创建 Python 虚拟环境...
    python -m venv .venv
    if errorlevel 1 exit /b 1
    .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
    if errorlevel 1 exit /b 1
) else (
    echo [1/4] Python 虚拟环境已就绪。
)

echo [2/4] 检查 Python 依赖...
.venv\Scripts\python.exe -c "import fastapi" 2>nul
if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 exit /b 1
)

if not exist .env (
    echo [提示] 未找到 .env，将使用默认配置。完整功能请先复制 .env.example 为 .env。
)

if not exist frontend\dist\index.html (
    where npm >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Node.js/npm。首次构建前端需要 Node.js 18 或更高版本。
        exit /b 1
    )
    echo [3/4] 构建前端...
    pushd frontend
    if not exist node_modules (
        call npm ci
        if errorlevel 1 (
            popd
            exit /b 1
        )
    )
    call npm run build
    if errorlevel 1 (
        popd
        exit /b 1
    )
    popd
) else (
    echo [3/4] 前端已构建。
)

if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"APP_HOST=" /c:"APP_PORT=" ".env"`) do (
        if /i "%%A"=="APP_HOST" if not defined APP_HOST set "APP_HOST=%%B"
        if /i "%%A"=="APP_PORT" if not defined APP_PORT set "APP_PORT=%%B"
    )
)
if not defined APP_HOST set "APP_HOST=127.0.0.1"
if not defined APP_PORT set "APP_PORT=8766"

echo [4/4] 启动 GroupBrief...
echo 访问地址：http://%APP_HOST%:%APP_PORT%
echo 按 Ctrl+C 停止服务。
echo.

.venv\Scripts\python.exe -m uvicorn app.main:app --host %APP_HOST% --port %APP_PORT%
set "GROUPBRIEF_EXIT=%ERRORLEVEL%"
endlocal & exit /b %GROUPBRIEF_EXIT%
