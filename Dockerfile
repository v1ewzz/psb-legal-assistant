# Веб-интерфейс + ядро. Одна команда: docker compose up
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Порт FastAPI (§18.2)
EXPOSE 8000

CMD ["uvicorn", "webapp:app", "--host", "0.0.0.0", "--port", "8000"]
