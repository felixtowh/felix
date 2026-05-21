FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/

# 创建数据目录
RUN mkdir -p /app/data /app/uploads /app/logs

# 暴露端口
EXPOSE 8088

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088", "--reload"]
