# Python суурьтай зураг ашиглана
FROM python:3.10-slim

# Ажиллах директор үүсгэнэ
WORKDIR /app

# Requirements болон app файлуудыг хуулах
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App source-г хуулах
COPY . .

# PORT тохируулах
EXPOSE 8000

# FastAPI-г ажиллуулах
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
