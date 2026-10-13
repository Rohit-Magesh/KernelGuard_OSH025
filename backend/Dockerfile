FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY sandbox/seccomp /app/seccomp
COPY demos /app/demos
COPY tests /app/tests
ENV SECCOMP_PATH=/app/seccomp/sandbox-seccomp.json DB_PATH=/data/sandbox.db DEMOS_DIR=/app/demos PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]