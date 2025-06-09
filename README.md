# 🤖 Chatwoot AI Assistant with Auto-Crawling

Энэ нь Chatwoot-тай холбогдсон AI туслах бөгөөд автоматаар сайт шүүрдэж, мэдээлэл хайх боломжтой.

## ✨ Онцлогууд

### 🚀 Автомат шүүрдэлт

- Server эхлэхэд автоматаар тохируулсан сайтыг шүүрдэнэ
- Хэрэглэгч `crawl` команд өгөхгүйгээр мэдээлэл бэлэн байна
- Background thread ашиглан performance-д нөлөөлөхгүй

### 🧠 Дэвшилтэт AI

- GPT-4.1 ашиглан монгол хэлээр хариулт өгдөг
- Яриаг санаж үлддэг (conversation memory)
- Шүүрдсэн мэдээллийг контекст болгон ашигладаг

### 🔍 Хайлтын систем

- Шүүрдсэн мэдээллээс хурдан хайлт
- Title болон агуулгаас хайдаг
- Товч snippet-ууд харуулна

## 🛠️ Тохиргоо

### Environment Variables

`.env` файл үүсгэн дараах тохиргоог хийнэ үү:

```bash
# Chatwoot тохиргоо
CHATWOOT_API_KEY=your_chatwoot_api_key_here
ACCOUNT_ID=your_account_id_here
CHATWOOT_BASE_URL=https://app.chatwoot.com

# OpenAI тохиргоо
OPENAI_API_KEY=your_openai_api_key_here

# Шүүрдэлтийн тохиргоо
ROOT_URL=https://docs.cloud.mn/
AUTO_CRAWL_ON_START=true
MAX_CRAWL_PAGES=50
DELAY_SEC=0.5
```

### Автомат шүүрдэлтийг идэвхгүй болгох

```bash
AUTO_CRAWL_ON_START=false
```

## 🤖 Comandууд

### Хэрэглэгчийн командууд

- `crawl` - Сайтыг шүүрдэх (хэрэгтэй бол)
- `scrape <URL>` - Тодорхой хуудас шүүрдэх
- `search <асуулт>` - Мэдээлэл хайх
- `help` эсвэл `тусламж` - Тусламж харуулах
- `баяртай` - Ярилцлага дуусгах

### Чөлөөт ярилцлага

Та ямар ч асуулт асууж болно. AI монгол хэлээр хариулна.

## 🌐 API Endpoints

### Crawl статус шалгах

```bash
GET /api/crawl-status
```

### Хүчээр шүүрдэх

```bash
POST /api/force-crawl
```

### API-аар хайлт хийх

```bash
POST /api/search
Content-Type: application/json

{
  "query": "хайх үг",
  "max_results": 5
}
```

### Health check

```bash
GET /health
```

### Crawl хийсэн өгөгдөл авах

```bash
GET /api/crawled-data?limit=10
```

## 🚀 Эхлүүлэх

1. Dependencies суулгах:

```bash
pip install flask requests openai beautifulsoup4 python-dotenv
```

2. Environment variables тохируулах

3. Аппликейшн эхлүүлэх:

```bash
python main.py
```

Server эхлэхэд автоматаар шүүрдэлт эхлэнэ (AUTO_CRAWL_ON_START=true бол).

## 📊 Статус мэдээлэл

Crawl-ийн статус:

- `not_started` - Эхлээгүй
- `running` - Шүүрдэж байна
- `completed` - Амжилттай дууссан
- `failed` - Алдаатай
- `disabled` - Идэвхгүй

## 🔧 Production deployment

Gunicorn ашиглан:

```bash
gunicorn main:app --bind 0.0.0.0:8080
```

Docker ашиглан:

```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8080"]
```

## 🛡️ Анхаарах зүйлс

- OpenAI API түлхүүр хэрэгтэй
- Chatwoot webhook URL тохируулах шаардлагатай
- MAX_CRAWL_PAGES-ыг хэтрүүлбэл удаан болно
- AUTO_CRAWL_ON_START=true бол startup удаан байж болно
