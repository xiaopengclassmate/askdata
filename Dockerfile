# 阶段一：构建前端（产物不打进源码仓库，构建时现做）
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com
COPY web/ ./
RUN npm run build

# 阶段二：运行时，FastAPI 直接托管 dist，不需要单独的 nginx
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY app/ ./app/
COPY db/ ./db/
COPY semantic_layer/ ./semantic_layer/
COPY --from=web /web/dist ./web/dist

# 首次启动没有库就灌一份演示数据（固定种子，可复现）
RUN python db/seed.py

ENV QUERY_MAX_ROWS=200 \
    QUERY_TIMEOUT_SECONDS=5

EXPOSE 8130

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8130/health',timeout=2)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8130"]
