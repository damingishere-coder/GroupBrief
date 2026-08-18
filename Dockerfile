# ===== GroupBrief V2 多阶段构建 =====
# Stage 1：Node 构建前端
# Stage 2：Python 运行后端 + 静态资源

# ---------- Stage 1: 前端构建 ----------
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
# 利用缓存：仅复制依赖清单
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
# 复制前端源码并构建
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: 后端运行 ----------
FROM python:3.12-slim
WORKDIR /app

# 时区（GroupBrief 使用 Asia/Shanghai；slim 镜像默认 UTC）
ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 安装依赖（含 tzdata 供 ZoneInfo 使用）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码 / 模板 / 配置 / 脚本 / 前端构建产物
COPY app/ ./app/
COPY templates/ ./templates/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# 运行时配置（可被 docker-compose / 环境变量覆盖）
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8766 \
    DATABASE_URL=sqlite:///data/groupbrief.db

EXPOSE 8766

# 容器内以 uvicorn 启动；--workers 1 避免 SQLite 多进程写冲突
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8766", "--workers", "1"]
