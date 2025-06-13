# Python суурьтай зураг
FROM python:3.10-slim

# Ажиллах директор үүсгэх
WORKDIR /app

# requirements.txt-ийг эхэлж хуулж, дараа нь багцуудыг суулгах
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source-г хуулна
COPY . .

# Порт нээх
EXPOSE 8000

# Gunicorn + Uvicorn Worker ашиглан FastAPI app ажиллуулах
CMD ["gunicorn", "main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
