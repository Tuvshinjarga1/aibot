# Python суурьтай зураг
FROM python:3.10-slim

# Ажиллах директор үүсгэх
WORKDIR /app

# Шаардлагатай файлуудыг хуулж суулгах
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App source-г хуулна
COPY . .

# Порт нээх (gunicorn default: 8000)
EXPOSE 8000

# Flask + Gunicorn ажиллуулах
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "main:app"]
