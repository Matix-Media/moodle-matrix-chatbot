FROM python:3.13-slim
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30"
COPY app.py /app/app.py
WORKDIR /app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
