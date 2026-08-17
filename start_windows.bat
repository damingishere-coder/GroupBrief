@echo off
chcp 65001 >nul
title 缇ゆ姤 GroupBrief
cd /d "%~dp0"

echo ============================================
echo  缇ゆ姤 GroupBrief V1 鍚姩鑴氭湰
echo ============================================

if not exist .venv\Scripts\python.exe (
    echo [1/3] 棣栨杩愯锛屽垱寤?Python 铏氭嫙鐜...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
)

echo [2/3] 妫€鏌?Python 渚濊禆...
.venv\Scripts\python.exe -c "import fastapi" 2>nul
if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

if not exist .env (
    echo [鎻愮ず] 鏈壘鍒?.env锛屽皢浣跨敤榛樿閰嶇疆锛堝彲澶嶅埗 .env.example 涓?.env 淇敼锛?)

if not exist frontend\dist\index.html (
    echo [3/3] 棣栨杩愯锛屾瀯寤哄墠绔〉闈?..
    pushd frontend
    if not exist node_modules ( call npm install )
    call npm run build
    popd
) else (
    echo [3/3] 鍓嶇宸叉瀯寤猴紝璺宠繃
)

echo.
echo 姝ｅ湪鍚姩 GroupBrief... 璇风◢鍊?echo 鎵撳紑娴忚鍣ㄨ闂? http://127.0.0.1:8766
echo 鎸?Ctrl+C 鍋滄鏈嶅姟
echo.

.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8766

pause

