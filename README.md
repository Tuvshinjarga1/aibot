# 📧 Имэйл баталгаажуулалттай Chatwoot Chatbot

Энэ систем нь хэрэглэгчдээс эхлээд имэйл хаягаа баталгаажуулахыг шаардаж, дараа нь OpenAI Assistant-той харилцах боломжийг олгодог.

## 🚀 Системийн ажиллагаа

1. **Хэрэглэгч Chatwoot chatbot-д мессеж бичнэ**
2. **Система имэйл хаяг шаардана**
3. **Хэрэглэгч имэйлээ оруулна**
4. **Система тухайн имэйл рүү баталгаажуулах линк илгээнэ**
5. **Хэрэглэгч линк дээр дарж баталгаажуулна**
6. **Баталгаажуулсны дараа chatbot ажиллаж эхэлнэ**

## 📋 Шаардлагатай зүйлс

### Python packages

```bash
pip install -r requirements.txt
```

### Орчны хувьсагчид

`env_example.txt` файлыг `.env` болгон хуулж, дараах мэдээллийг оруулна уу:

- **OpenAI**: API key болон Assistant ID
- **Chatwoot**: API key болон Account ID
- **Gmail**: Имэйл болон App Password
- **JWT Secret**: Аюулгүй түлхүүр үг
- **Verification URL**: Таны сервэрийн хаяг

### Gmail тохиргоо

1. Gmail дээр 2-Factor Authentication идэвхжүүлэх
2. App Password үүсгэх
3. `SENDER_EMAIL` болон `SENDER_PASSWORD` тохируулах

## 🔧 Суулгах заавар

```bash
# 1. Repository клон хийх
git clone <your-repo>
cd <your-repo>

# 2. Dependencies суулгах
pip install -r requirements.txt

# 3. Environment variables тохируулах
cp env_example.txt .env
# .env файлыг засварлаж өөрийн мэдээллээр дүүргэх

# 4. Серверийг ажиллуулах
python main.py
```

## 📡 Webhook тохиргоо

Chatwoot дээр webhook URL тохируулах:

```
http://your-domain.com/webhook
```

## 🎯 Ашиглах заавар

### Хэрэглэгчийн хувьд:

1. Chatwoot chatbot-д дурын мессеж бичих
2. "Зөв имэйл хаягаа бичээд илгээнэ үү" гэх мессеж ирнэ
3. Имэйл хаягаа бичих (жишээ: `user@gmail.com`)
4. "Имэйл рүү баталгаажуулах линк илгээлээ" гэх мессеж ирнэ
5. Имэйлээ шалгаад линк дээр дарах
6. "Амжилттай баталгаажлаа" хуудас харагдана
7. Chatwoot дээр "Таны имэйл баталгаажлаа!" мессеж ирнэ
8. Одоо chatbot-той чатлаж болно

### Техникийн дэлгэрэнгүй:

- Хэрэглэгч бүр өөрийн OpenAI thread-тэй
- Имэйл баталгаажуулах токен 24 цагийн дараа дуусна
- Chatwoot conversation дээр `email_verified` болон `verified_contact_{contact_id}` хадгалагдана

## 🔒 Аюулгүй байдал

- JWT токен ашиглан имэйл баталгаажуулах
- Токен 24 цагийн дараа автоматаар дуусна
- Хэрэглэгч тус бүрийн thread тусгаарлагдмал
- Имэйл format шалгагддаг

## 🐛 Алдаа засварлах

Хэрэв асуудал гарвал:

1. Console логуудыг шалгах
2. Environment variables зөв тохируулсан эсэхийг шалгах
3. Gmail App Password зөв ашиглаж байгаа эсэхийг шалгах
4. Chatwoot webhook URL зөв тохируулсан эсэхийг шалгах

## 📞 Дэмжлэг

Асуулт байвал issue үүсгэнэ үү.

# Chatwoot AI Chatbot + RAG Документ Систем

## Ерөнхий тойм

Энэ систем нь **Chatwoot** платформд зориулсан **AI chatbot** бөгөөд **RAG (Retrieval-Augmented Generation)** технологи ашиглан документын хайлтыг интеграци хийсэн юм. Систем нь хэрэглэгчийн асуултыг автоматаар хариулж, шаардлагатай үед ажилтанд дамжуулдаг.

## Гол онцлогууд

### 🤖 AI Chatbot

- **OpenAI Assistant API** ашиглан хэрэглэгчтэй автомат харилцах
- Имэйл баталгаажуулалт шаардах
- Microsoft Teams-ээр ажилтанд мэдээлэх
- Retry механизм болон алдаа удирдлага

### 📚 RAG Документ Систем

- **LangChain + FAISS** ашиглан векторын хайлт
- Документ сайтаас автомат мэдээлэл цуглуулах
- Хэрэглэгчийн асуултыг документаас хайж хариулах
- Source links-тэй хариулт өгөх

### 🔗 Интеграци онцлогууд

- Документын асуулт бол RAG систем ашиглах
- Ерөнхий асуулт бол AI Assistant ашиглах
- Автомат система сонголт
- Teams мэдээлэл зөвхөн AI Assistant-д

## API Endpoints

### Үндсэн endpoints

- `POST /webhook` - Chatwoot webhook handler
- `GET /verify` - Имэйл баталгаажуулалт
- `GET /health` - Системийн health check

### RAG системийн endpoints

- `POST /docs-search` - Документ хайлтын тусдаа API
- `POST /rebuild-docs` - Документын vector store дахин бүтээх

### Тест endpoints

- `GET /test-teams` - Teams мэдээлэл тест

## Суулгах заавар

### 1. Dependencies суулгах

```bash
pip install -r requirements.txt
```

### 2. Орчны хувьсагчид тохируулах

```bash
# OpenAI тохиргоо
OPENAI_API_KEY=sk-...
ASSISTANT_ID=asst_...

# Chatwoot тохиргоо
CHATWOOT_API_KEY=your_chatwoot_token
ACCOUNT_ID=1

# Email тохиргоо
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-app-password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587

# Teams тохиргоо (сонголт)
TEAMS_WEBHOOK_URL=https://...

# RAG тохиргоо
DOCS_BASE_URL=https://docs.cloud.mn

# JWT тохиргоо
JWT_SECRET=your-secret-key
VERIFICATION_URL_BASE=http://localhost:5000
```

### 3. Ажиллуулах

```bash
python main.py
```

## Системийн ажиллагаа

### 1. Хэрэглэгчийн асуулт ирэх

```
Webhook → Баталгаажуулалт шалгах → Асуултын төрөл тодорхойлох
```

### 2. Документын асуулт

```
RAG систем → Документ хайх → Хариулт + Sources → Chatwoot
```

### 3. Ерөнхий асуулт

```
AI Assistant → OpenAI API → Хариулт → Teams мэдээлэл → Chatwoot
```

## RAG Системийн бүтэц

### Документ цуглуулалт

- BeautifulSoup ашиглан web scraping
- Автомат link дагаж цуглуулах
- Title болон content тусгаарлах

### Vector Store

- LangChain RecursiveCharacterTextSplitter
- OpenAI Embeddings
- FAISS векторын сан
- Дискэнд хадгалагдах

### Хайлтын механизм

- Similarity search
- Top 3 документ олох
- Source metadata хадгалах

## Teams интеграци

### Мэдээллийн дүрүүд

- 📋 Хэрэглэгчийн асуудлын дүгнэлт
- 🤖 AI систем алдаа
- 🔄 Retry бүтэлгүйтэх
- 📊 Асуудлын статистик

### Adaptive Cards формат

- Тодорхой мэдээлэл
- Chatwoot руу шилжих товч
- Цаг хугацааны тэмдэг

## Хэрэглээний жишээ

### Документын асуулт

```
Хэрэглэгч: "API хэрхэн ашиглах вэ?"
→ RAG систем ажиллана
→ Документаас хайж олно
→ Хариулт + docs.cloud.mn links
```

### Ерөнхий асуулт

```
Хэрэглэгч: "Сайн байна уу? Тусламж хэрэгтэй байна"
→ AI Assistant ажиллана
→ OpenAI хариулт
→ Teams мэдээлэл илгээх
```

## Монитинг

### Health Check

```bash
curl http://localhost:5000/health
```

### RAG тест

```bash
curl -X POST http://localhost:5000/docs-search \
  -H "Content-Type: application/json" \
  -d '{"question": "API documentation"}'
```

### Teams тест

```bash
curl http://localhost:5000/test-teams
```

## Системийн архитектур

```
Chatwoot → Flask Webhook → Email Check → Question Type
                                             ↓
                              ┌─ RAG System (docs questions)
                              └─ AI Assistant (general questions)
                                             ↓
                              Response → Teams (if needed) → Chatwoot
```

## Анхаарах зүйлс

1. **Vector Store** эхний удаа бүтээхэд удаан болно
2. **OpenAI rate limits** анхаарах
3. **Teams webhook** сонгогдсон тохиргоо
4. **Email SMTP** зөв тохируулах шаардлагатай
5. **DOCS_BASE_URL** документын сайт байх ёстой

## Хөгжүүлэлт

Системийг хөгжүүлэхийн тулд:

- Шинэ документын сайт нэмэх
- RAG prompt сайжруулах
- Teams notification форматыг өөрчлөх
- Monitoring нэмэх

---

**Анхаарах:** Энэ систем production орчинд ашиглахын өмнө бүх тохиргоог сайтар шалгана уу.

# Chatwoot AI Bot with Delayed Response

Энэ бол Chatwoot-той холбогдсон AI туслах бот юм. RAG (Retrieval-Augmented Generation) систем болон OpenAI Assistant ашиглан хэрэглэгчдэд хариулт өгдөг.

## 🚀 Шинэ функцууд

### ⏰ Delayed Response (Хойшлуулсан хариулт)

- Бот хариулахаас өмнө тодорхой хугацаа хүлээнэ
- Chatwoot inbox дээр мессеж харагдсаны дараа хариулна
- Typing indicator харуулж хэрэглэгчид мэдэгдэнэ

### 👤 Contact Management

- Хэрэглэгч имэйл өгөх үед автоматаар contact бүртгэнэ
- Баталгаажуулсан хэрэглэгчдийг тусгайлан тэмдэглэнэ

### 🤖 GPT-powered Escalation

- Teams руу асуудал явуулахдаа GPT дүгнэлт хийлгэнэ
- Илүү ухаалаг escalation логик

## 📋 Орчны хувьсагчид

```bash
# Үндсэн тохиргоо
OPENAI_API_KEY=your_openai_api_key
ASSISTANT_ID=your_assistant_id
CHATWOOT_API_KEY=your_chatwoot_api_key
ACCOUNT_ID=your_account_id

# Bot хариулах хугацаа (секундээр)
BOT_RESPONSE_DELAY=3

# RAG систем
DOCS_BASE_URL=https://docs.cloud.mn

# Email тохиргоо
SENDER_EMAIL=your_email@gmail.com
SENDER_PASSWORD=your_app_password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587

# Teams webhook
TEAMS_WEBHOOK_URL=your_teams_webhook_url

# JWT тохиргоо
JWT_SECRET=your_secret_key
VERIFICATION_URL_BASE=http://localhost:5000
```

## 🛠️ Суулгах заавар

1. **Dependencies суулгах:**

```bash
pip install flask openai requests beautifulsoup4 python-dotenv langchain langchain-community langchain-openai faiss-cpu PyJWT
```

2. **.env файл үүсгэх:**

```bash
cp .env.example .env
# .env файлд өөрийн мэдээллийг оруулах
```

3. **Ажиллуулах:**

```bash
python main.py
```

## 📡 API Endpoints

### 1. Webhook

```
POST /webhook
```

Chatwoot-аас ирэх мессежүүдийг боловсруулна.

### 2. Имэйл баталгаажуулалт

```
GET /verify?token=<verification_token>
```

Хэрэглэгчийн имэйл хаягийг баталгаажуулна.

### 3. Тохиргоо

```
GET /config
POST /config
```

**GET /config** - Одоогийн тохиргоог харах:

```json
{
  "bot_response_delay": 3,
  "max_ai_retries": 2,
  "rag_system_enabled": true,
  "teams_webhook_enabled": true,
  "email_enabled": true,
  "docs_base_url": "https://docs.cloud.mn",
  "vector_store_exists": true
}
```

**POST /config** - Тохиргоо өөрчлөх:

```json
{
  "bot_response_delay": 5
}
```

### 4. Документ хайлт

```
POST /docs-search
```

```json
{
  "question": "CloudMN-ийн талаар асуулт"
}
```

### 5. Health Check

```
GET /health
```

### 6. Vector Store дахин бүтээх

```
POST /rebuild-docs
```

## ⚙️ Тохиргооны параметрүүд

### BOT_RESPONSE_DELAY

- **Утга:** 1-30 секунд
- **Default:** 3 секунд
- **Тайлбар:** Бот хариулахаас өмнө хэдэн секунд хүлээх

### MAX_AI_RETRIES

- **Утга:** 0-5
- **Default:** 2
- **Тайлбар:** AI алдаа гарвал хэдэн удаа дахин оролдох

## 🔄 Ажиллах процесс

1. **Мессеж ирэх:** Chatwoot webhook дуудагдана
2. **Баталгаажуулалт:** Хэрэглэгчийн имэйл баталгаажсан эсэхийг шалгана
3. **Delayed Response:** Тохируулсан хугацаа хүлээнэ
4. **Typing Indicator:** Хэрэглэгчид "typing..." харуулна
5. **AI Processing:** RAG болон OpenAI Assistant зэрэг ажиллана
6. **Response:** Хариултыг Chatwoot руу илгээнэ
7. **Escalation:** Шаардлагатай бол Teams руу мэдээлнэ

## 🎯 Давуу талууд

- ✅ Chatwoot inbox дээр мессеж харагдсаны дараа хариулна
- ✅ Typing indicator-ээр хэрэглэгчид мэдэгдэнэ
- ✅ Contact автоматаар бүртгэгдэнэ
- ✅ GPT дүгнэлттэй Teams мэдээлэл
- ✅ Тохируулж болох хариулах хугацаа
- ✅ Илүү сайн хэрэглэгчийн туршлага

## 🔧 Тест хийх

### Тохиргоо шалгах:

```bash
curl http://localhost:5000/config
```

### Bot response delay өөрчлөх:

```bash
curl -X POST http://localhost:5000/config \
  -H "Content-Type: application/json" \
  -d '{"bot_response_delay": 5}'
```

### Health check:

```bash
curl http://localhost:5000/health
```

## 📝 Тэмдэглэл

- Typing indicator нь Chatwoot API-аас хамаарна
- Delayed response нь background thread-д ажиллана
- Contact бүртгэл нь имэйл баталгаажуулалттай холбоотой
- Teams мэдээлэл нь GPT дүгнэлттэй илгээгдэнэ

## 🐛 Алдаа засварлалт

Хэрэв typing indicator ажиллахгүй бол:

1. Chatwoot API key-г шалгана уу
2. Account ID зөв эсэхийг шалгана уу
3. Chatwoot дээр conversation идэвхтэй эсэхийг шалгана уу
