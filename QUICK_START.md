# 🚀 Хурдан Эхлэх Заавар - Chatwoot AI + Teams

## 📝 Тойм

Энэ заавар нь Chatwoot AI Chatbot + RAG систем + Microsoft Teams integration-ийг 15 минутад суулгах зорилготой.

---

## ⚡ 5 Алхмын Хурдан Суулгалт

### 🔧 Алхам 1: Environment тохируулах (3 мин)

```bash
# Repository татах
git clone <your-repo-url>
cd chatwoot-ai

# Dependencies суулгах
pip install -r requirements.txt

# .env файл үүсгэх
cp .env.example .env
```

**`.env` файл бөглөх:**

```bash
# OpenAI (шаардлагатай)
OPENAI_API_KEY=sk-proj-...
ASSISTANT_ID=asst_...

# Chatwoot (шаардлагатай)
CHATWOOT_API_KEY=your_token
ACCOUNT_ID=1

# Email (шаардлагатай)
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-app-password

# Teams (сонголт)
TEAMS_WEBHOOK_URL=https://yourcompany.webhook.office.com/...

# RAG Документ (сонголт)
DOCS_BASE_URL=https://docs.cloud.mn
```

### 📱 Алхам 2: Teams Webhook үүсгэх (5 мин)

1. **Microsoft Teams** нээх
2. **Channel** → **"..."** → **"Connectors"**
3. **"Incoming Webhook"** хайж олох
4. **Name:** `AI Customer Support`
5. **URL хуулж .env-д оруулах**

### 🧪 Алхам 3: Teams тест (2 мин)

```bash
# Teams холболт тест
export TEAMS_WEBHOOK_URL="https://yourcompany.webhook.office.com/..."
python teams_setup_example.py
```

**Хүлээгдэж буй гарц:**

```
✅ Энгийн мессеж амжилттай илгээлээ!
✅ Adaptive Card мессеж амжилттай илгээлээ!
✅ Хэрэглэгчийн асуудлын дүгнэлт амжилттай илгээлээ!
```

### 🚀 Алхам 4: Систем ажиллуулах (2 мин)

```bash
# Local ажиллуулах
python main.py

# Production (Docker)
docker-compose up -d
```

### ✅ Алхам 5: Эцсийн тест (3 мин)

1. **Chatwoot дээр** шинэ conversation эхлүүлэх
2. **Email:** `test@example.com` бичиж баталгаажуулах
3. **Асуулт:** `Сайн байна уу? Тусламж хэрэгтэй`
4. **Teams дээр мэдээлэл ирэхийг шалгах**

---

## 🎯 Хурдан Тохиргооны Checklist

### Үндсэн тохиргоо

- [ ] Python 3.10+ суулгасан
- [ ] `pip install -r requirements.txt` амжилттай
- [ ] `.env` файл бүх утгатай бөглөсөн
- [ ] OpenAI API key ажиллаж байна
- [ ] Chatwoot API холболт амжилттай

### Teams Integration

- [ ] Teams-д Incoming Webhook үүсгэсэн
- [ ] `TEAMS_WEBHOOK_URL` environment variable тохируулсан
- [ ] `python teams_setup_example.py` амжилттай ажиллав
- [ ] Teams channel дээр тест мессеж ирсэн

### RAG Систем

- [ ] `DOCS_BASE_URL` тохируулсан (сонголт)
- [ ] Vector store үүсч байна (анхны ажиллуулалтад)
- [ ] Документын асуултад RAG хариулт ирж байна

---

## 🆘 Түгээмэл асуудал + Шийдэл

### ❌ `ModuleNotFoundError`

```bash
pip install -r requirements.txt --upgrade
```

### ❌ `OpenAI API key not found`

```bash
# .env файлд
OPENAI_API_KEY=sk-proj-your-actual-key
```

### ❌ `Teams webhook алдаа`

```bash
# URL зөв эсэхийг шалгах
curl -X POST https://your-webhook-url \
  -H "Content-Type: application/json" \
  -d '{"text":"test"}'
```

### ❌ `FAISS loading алдаа`

```bash
pip install faiss-cpu --force-reinstall
```

### ❌ `Worker Timeout` (Production)

```bash
# Gunicorn timeout нэмэх
gunicorn main:app --timeout 120 --workers 1
```

---

## 🔍 Health Checks

### Систем статус

```bash
curl http://localhost:5000/health
```

### Teams холболт

```bash
curl http://localhost:5000/test-teams
```

### RAG систем тест

```bash
curl -X POST http://localhost:5000/docs-search \
  -H "Content-Type: application/json" \
  -d '{"question": "API гэж юу вэ?"}'
```

---

## 📊 Ажиллагааны логик

### 1. Хэрэглэгчийн мессеж ирэх

```
Chatwoot Webhook → Email баталгаажуулалт → Асуултын төрөл тодорхойлох
```

### 2. Документын асуулт

```
RAG Систем → Документ хайх → Хариулт + Sources → Chatwoot
```

### 3. Ерөнхий асуулт

```
AI Assistant → OpenAI → Teams мэдээлэл (шаардлагатай бол) → Chatwoot
```

---

## 🎨 Нэмэлт тохиргоо

### Teams мэдээллийн давтамж

```python
# main.py дотор
MAX_AI_RETRIES = 2  # AI хэдэн удаа оролдсоны дараа Teams-д мэдээлэх
```

### RAG документын сайт өөрчлөх

```bash
# .env дотор
DOCS_BASE_URL=https://your-docs-site.com
```

### Email SMTP өөрчлөх

```bash
# .env дотор (Gmail бус)
SMTP_SERVER=mail.company.com
SMTP_PORT=587
```

---

## 🚀 Production Deployment

### Docker Compose

```yaml
version: "3.8"
services:
  chatbot:
    build: .
    environment:
      - TEAMS_WEBHOOK_URL=${TEAMS_WEBHOOK_URL}
    ports:
      - "5000:5000"
    volumes:
      - ./docs_faiss_index:/app/docs_faiss_index
```

### Railway/Heroku

```bash
# Environment variables нэмэх
TEAMS_WEBHOOK_URL=https://...
OPENAI_API_KEY=sk-...
CHATWOOT_API_KEY=...
```

---

## 📞 Тусламж

**Асуудал гарвал:**

- 📖 `TEAMS_INTEGRATION_SETUP.md` уншаарай
- 🧪 `python teams_setup_example.py` ажиллуулаарай
- 🔍 `curl http://localhost:5000/health` шалгаарай
- 📝 GitHub дээр Issue үүсгээрэй

**Бүх тест амжилттай бол систем бэлэн! 🎉**
