# Microsoft Teams Интеграци - Алхам Алхмын Зааварчилгаа

## 📋 Ерөнхий тойм

Энэ зааварчилгаа нь таны Chatwoot AI Chatbot системийг Microsoft Teams-тэй холбоход шаардлагатай бүх алхмуудыг дэлгэрүүлэн тайлбарласан.

## 🎯 Хүрэх зорилго

Teams холболтын дараа:

- ✅ Хэрэглэгчийн асуудлыг AI дүгнэж Teams-д илгээх
- ✅ AI алдаа гарвал Teams-д мэдээлэх
- ✅ Ажилтан Teams дээрээс Chatwoot руу шилжих
- ✅ Structured мэдээлэл Adaptive Cards форматаар харах

---

## 🚀 АЛХАМ 1: Microsoft Teams дээр Webhook үүсгэх

### 1.1 Teams Application рүү орох

1. **Microsoft Teams** апп-ыг нээх (веб эсвэл desktop)
2. Ажиллахыг хүссэн **Team**-ээ сонгох
3. Тохирох **Channel**-ээ сонгох (жишээ: "Customer Support")

### 1.2 Incoming Webhook суулгах

1. Channel нэрийн хажууд **"..."** (More options) дарах
2. **"Connectors"** эсвэл **"Manage Channel"** сонгох
3. **"Apps"** хэсгээс **"Incoming Webhook"** хайж олох
4. **"Add"** эсвэл **"Configure"** дарах

### 1.3 Webhook тохируулах

```
Webhook Name: AI Customer Support Alerts
Description: Хэрэглэгчийн асуудлын дүгнэлт
```

5. **"Create"** дарах
6. **Webhook URL**-г хуулж авах (энэ чухал!)

```
Жишээ URL:
https://yourcompany.webhook.office.com/webhookb2/xxxxx-xxxx-xxxx-xxxx-xxxxxxxxx/IncomingWebhook/yyyyy/zzzzzz
```

---

## 🔧 АЛХАМ 2: Environment Variables тохируулах

### 2.1 .env файл засах

```bash
# Одоо байгаа тохиргоонууд
OPENAI_API_KEY=sk-...
ASSISTANT_ID=asst_...
CHATWOOT_API_KEY=your_chatwoot_token
ACCOUNT_ID=1
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-app-password

# ШИНЭ НЭМЭХ: Teams Webhook
TEAMS_WEBHOOK_URL=https://yourcompany.webhook.office.com/webhookb2/...

# RAG тохиргоо
DOCS_BASE_URL=https://docs.cloud.mn

# JWT тохиргоо
JWT_SECRET=your-secret-key-here
VERIFICATION_URL_BASE=http://localhost:5000
```

### 2.2 Production орчинд deployment

**Docker Docker Compose:**

```yaml
environment:
  - TEAMS_WEBHOOK_URL=https://yourcompany.webhook.office.com/...
```

**Railway/Heroku:**

```bash
TEAMS_WEBHOOK_URL=https://yourcompany.webhook.office.com/...
```

---

## 🧪 АЛХАМ 3: Холболт тест хийх

### 3.1 Test endpoint ашиглах

```bash
# Local тест
curl http://localhost:5000/test-teams

# Production тест
curl https://your-app-domain.com/test-teams
```

### 3.2 Хүлээгдэж буй хариулт

**Амжилттай:**

```json
{
  "status": "success",
  "message": "Teams мэдээлэл амжилттай илгээлээ!"
}
```

**Алдаа:**

```json
{
  "error": "TEAMS_WEBHOOK_URL тохируулаагүй байна"
}
```

---

## 📱 АЛХАМ 4: Бодит тест хийх

### 4.1 Chatwoot дээр хэрэглэгч мессеж илгээх

1. Chatwoot дээр шинэ conversation эхлүүлэх
2. Email баталгаажуулах (test@example.com)
3. Ямар нэг асуулт асуух

### 4.2 Teams дээр мэдээлэл ирэх

Teams channel дээр дараах мэдээлэл ирэх ёстой:

```
📋 Хэрэглэгчийн асуудлын дүгнэлт

AI систем хэрэглэгчийн асуудлыг дүгнэж, ажилтны анхаарал татахуйц асуудал гэж үзэж байна.

Харилцагч: test@example.com
Хэрэглэгчийн мессеж: Сайн байна уу? Тусламж хэрэгтэй байна
Хугацаа: 2024-01-28 14:30:00

🤖 AI Дүгнэлт:
АСУУДЛЫН ТӨРӨЛ: Мэдээллийн хүсэлт
ЯАРАЛТАЙ БАЙДАЛ: Дунд
ТОВЧ ТАЙЛБАР: Хэрэглэгч ерөнхий тусламж хүсч байна
ШААРДЛАГАТАЙ АРГА ХЭМЖЭЭ: Анхаарал хандуулах

[Chatwoot дээр харах] товч
```

---

## ⚙️ АЛХАМ 5: Teams мэдээллийн тохиргоо

### 5.1 Хэзээ Teams мэдээлэл ирэх

```python
# main.py дотор тохируулж болох
MAX_AI_RETRIES = 2  # AI хэдэн удаа оролдсоны дараа Teams-д мэдээлэх
```

### 5.2 Teams мэдээллийн төрлүүд

1. **Анхны асуулт** → Заавал Teams-д илгээх
2. **Шинэ төрлийн асуудал** → Teams-д илгээх
3. **AI систем алдаа** → Teams-д илгээх
4. **Дагалдах асуулт** → Teams-д илгээхгүй
5. **RAG хариулт** → Teams-д илгээхгүй

---

## 🔍 АЛХАМ 6: Monitoring болон Debugging

### 6.1 Health Check

```bash
curl http://localhost:5000/health
```

Хариулт:

```json
{
  "status": "ok",
  "timestamp": "2024-01-28T14:30:00.000Z",
  "components": {
    "rag_system": true,
    "openai_client": true,
    "teams_webhook": true,
    "email_smtp": true,
    "chatwoot_api": true
  }
}
```

### 6.2 Teams webhook тест

```bash
curl http://localhost:5000/test-teams
```

### 6.3 Log файл шалгах

```bash
# Docker logs
docker logs your-container-name

# Local run logs
python main.py
```

**Хүлээгдэж буй логууд:**

```
✅ Teams техникийн мэдээлэл илгээлээ: Анхны асуулт
❌ Teams мэдээлэл илгээхэд алдаа: [error message]
⏭️ Өмнөх асуудлын үргэлжлэл - Teams-д илгээхгүй
```

---

## 🛠 АЛХАМ 7: Production Deployment

### 7.1 Docker Compose өөрчлөх

```yaml
# docker-compose.yml
version: "3.8"
services:
  chatbot:
    build: .
    environment:
      - TEAMS_WEBHOOK_URL=${TEAMS_WEBHOOK_URL}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      # ... бусад env vars
    ports:
      - "5000:5000"
```

### 7.2 Environment file

```bash
# .env.production
TEAMS_WEBHOOK_URL=https://yourcompany.webhook.office.com/webhookb2/...
OPENAI_API_KEY=sk-...
# ... бусад утгууд
```

### 7.3 Deploy хийх

```bash
# Docker Compose
docker-compose --env-file .env.production up -d

# Manual deploy
export TEAMS_WEBHOOK_URL="https://yourcompany.webhook.office.com/..."
python main.py
```

---

## 🎨 АЛХАМ 8: Teams мэдээллийг customize хийх

### 8.1 Adaptive Card өөрчлөх

`main.py` дотор `send_teams_notification` функцийг засч болно:

```python
# Өнгө өөрчлөх
"color": "Attention"  # Good, Warning, Accent

# Шинэ field нэмэх
{
    "title": "Систем:",
    "value": "AI Chatbot v2.0"
}

# Товч нэмэх
{
    "type": "Action.OpenUrl",
    "title": "Дэлгэрэнгүй харах",
    "url": f"{CHATWOOT_BASE_URL}/conversations/{conv_id}"
}
```

### 8.2 Notification frequency

```python
# Хэрэв давтан асуулт ирэхээс сэргийлэх
def should_escalate_to_teams(thread_id, current_message):
    # Энэ функцийг өөрчилж болно
    # Жишээ: 10 минутын дотор 1 удаа л илгээх
```

---

## 🚨 Анхаарах зүйлс

### Security

1. **Webhook URL** хуваалцахгүй
2. **Environment variables** secure байлгах
3. **HTTPS** ашиглах production-д

### Performance

1. **OpenAI API rate limits** анхаарах
2. **Teams webhook rate limits** анхаарах (30 req/min)
3. **Timeout алдаа** боломжтой

### Error Handling

```python
# main.py дотор
try:
    send_teams_notification(...)
except Exception as e:
    print(f"❌ Teams мэдээлэл илгээхэд алдаа: {e}")
    # Fallback mechanism
```

---

## 🆘 Түгээмэл алдаанууд ба шийдэл

### Алдаа 1: "TEAMS_WEBHOOK_URL тохируулаагүй"

**Шийдэл:**

```bash
# .env файлд нэмэх
TEAMS_WEBHOOK_URL=https://your-webhook-url

# Environment variable export хийх
export TEAMS_WEBHOOK_URL="https://your-webhook-url"
```

### Алдаа 2: "HTTP 400 Bad Request"

**Шалгах зүйлс:**

- Webhook URL зөв эсэх
- JSON format зөв эсэх
- Teams-д Incoming Webhook идэвхтэй эсэх

### Алдаа 3: "HTTP 429 Too Many Requests"

**Шийдэл:**

- Teams webhook rate limit (30/min)
- `should_escalate_to_teams` логик сайжруулах
- Caching механизм нэмэх

### Алдаа 4: "Worker Timeout"

**Шийдэл:**

```python
# main.py дотор timeout нэмэх
response = client.chat.completions.create(
    timeout=15  # seconds
)
```

---

## 📞 Дэмжлэг авах

Хэрэв асуудал гарвал:

1. **GitHub Issue** үүсгэх
2. **Log файлуудыг** хавсаргах
3. **Environment setup**-ээ шалгах
4. **Teams webhook URL**-г дахин тест хийх

---

## ✅ Checklist - Teams Integration бэлэн эсэх

- [ ] Teams дээр Incoming Webhook үүсгэсэн
- [ ] TEAMS_WEBHOOK_URL environment variable тохируулсан
- [ ] `/test-teams` endpoint амжилттай
- [ ] Chatwoot дээр тест мессеж илгээж Teams-д мэдээлэл ирсэн
- [ ] AI дүгнэлт зөв форматтай ирж байна
- [ ] "Chatwoot дээр харах" товч ажиллаж байна
- [ ] Production environment-д deploy хийсэн

**Бүгд ✅ бол Teams integration бэлэн! 🎉**
