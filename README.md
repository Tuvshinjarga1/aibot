# 📧 Имэйл баталгаажуулалттай Chatwoot Chatbot

Энэ систем нь хэрэглэгчдээс эхлээд имэйл хаягаа баталгаажуулахыг шаардаж, дараа нь OpenAI Assistant-той харилцах боломжийг олгодог.

## 🚀 Системийн ажиллагаа

1. **Хэрэглэгч Chatwoot chatbot-д мессеж бичнэ**
2. **Система хэрэглэгчийн мессежийг Chatwoot дээр харагдуулна** (API channel-д зориулсан)
3. **Система имэйл хаяг шаардана**
4. **Хэрэглэгч имэйлээ оруулна**
5. **Система тухайн имэйл рүү баталгаажуулах линк илгээнэ**
6. **Хэрэглэгч линк дээр дарж баталгаажуулна**
7. **Баталгаажуулсны дараа chatbot ажиллаж эхэлнэ**

## 📋 Шаардлагатай зүйлс

### Python packages

```bash
pip install -r requirements.txt
```

### Орчны хувьсагчид

`env_example.txt` файлыг `.env` болгон хуулж, дараах мэдээллийг оруулна уу:

- **OpenAI**: API key болон Assistant ID
- **Chatwoot**: API key, Account ID, Inbox ID (API channel)
- **Gmail**: Имэйл болон App Password
- **JWT Secret**: Аюулгүй түлхүүр үг
- **Verification URL**: Таны сервэрийн хаяг

### Chatwoot API Channel тохиргоо

1. Chatwoot дээр API channel үүсгэх
2. Inbox ID-г `.env` файлд `INBOX_ID` болгон оруулах
3. Webhook URL тохируулах: `http://your-domain.com/webhook`
4. Inbox шүүлт тохируулах: `FILTER_BY_INBOX=true`

**Inbox шүүлтийн тохиргоо:**

- `FILTER_BY_INBOX=true` - Зөвхөн `INBOX_ID`-тай тохирох inbox-аас мессеж боловсруулах
- `FILTER_BY_INBOX=false` - Бүх inbox-аас мессеж боловсруулах
- Хэрэв `INBOX_ID` тохируулаагүй бол бүх inbox-аас мессеж боловсруулна

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

## 📡 API Endpoints

### Үндсэн endpoints

- `POST /webhook` - Chatwoot webhook handler
- `GET /verify` - Имэйл баталгаажуулалт
- `GET /health` - Системийн health check
- `GET /inboxes` - Бүх inbox-уудын жагсаалт авах

### Тест endpoints

- `POST /test-user-message` - Хэрэглэгчийн мессеж үүсгэх тест
- `POST /docs-search` - RAG документ хайлт
- `POST /rebuild-docs` - Документын vector store дахин бүтээх

## 🎯 Ашиглах заавар

### Хэрэглэгчийн хувьд:

1. Chatwoot chatbot-д дурын мессеж бичих
2. Система автоматаар хэрэглэгчийн мессежийг Chatwoot дээр харагдуулна
3. "Зөв имэйл хаягаа бичээд илгээнэ үү" гэх мессеж ирнэ
4. Имэйл хаягаа бичих (жишээ: `user@gmail.com`)
5. "Имэйл рүү баталгаажуулах линк илгээлээ" гэх мессеж ирнэ
6. Имэйлээ шалгаад линк дээр дарах
7. "Амжилттай баталгаажлаа" хуудас харагдана
8. Chatwoot дээр "Таны имэйл баталгаажлаа!" мессеж ирнэ
9. Одоо chatbot-той чатлаж болно

### API Channel тохиргоо:

Chatwoot SaaS ашиглаж байгаа бол:

1. **Settings > Inboxes > Add Inbox > API** сонгох
2. **Inbox name** оруулах
3. **Webhook URL**: `http://your-domain.com/webhook`
4. **Inbox ID**-г хуулж `.env` файлд оруулах

### Техникийн дэлгэрэнгүй:

- Хэрэглэгч бүр өөрийн OpenAI thread-тэй
- Имэйл баталгаажуулах токен 24 цагийн дараа дуусна
- API channel-д хэрэглэгчийн мессеж автоматаар харагдуулагдана
- RAG + AI Assistant хоёр систем зэрэг ажиллана

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
5. API channel-ийн Inbox ID зөв тохируулсан эсэхийг шалгах

### Хэрэглэгчийн мессеж харагдахгүй байвал:

1. `/health` endpoint-оор системийн статусыг шалгах
2. Chatwoot API channel зөв тохируулсан эсэхийг шалгах
3. `INBOX_ID` орчны хувьсагч зөв тохируулсан эсэхийг шалгах
4. `/test-user-message` endpoint ашиглан тест хийх

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
- `GET /inboxes` - Бүх inbox-уудын жагсаалт авах

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
