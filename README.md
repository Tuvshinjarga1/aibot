# CloudMN RAG Chatbot System

CloudMN docs.cloud.mn сайтыг crawl хийж RAG (Retrieval-Augmented Generation) системтэй GPT chatbot. Chatwoot-той интеграцилагдсан.

## Онцлог шинж чанарууд

- 📧 **Имэйл баталгаажуулалт**: Хэрэглэгч эхлээд имэйлээ баталгаажуулна
- 🤖 **AI Chatbot**: OpenAI Assistant-тай холболт
- 📚 **RAG System**: CloudMN docs сайтаас мэдээлэл олж хариулна
- 🔍 **Web Crawling**: docs.cloud.mn сайтыг автоматаар crawl хийх
- 💾 **Vector Database**: FAISS ашиглан embedding хадгалах
- 📱 **Teams Integration**: Техникийн асуудлыг Microsoft Teams-д мэдээлэх
- 🔄 **Cache System**: Crawl хийсэн мэдээллийг cache хийх
- 🧠 **Smart Query Logic**: Ижил төрлийн асуултанд нэг удаадаа URL өгч, шинэ асуудалд л шинэ хайлт хийх
- ⚡ **Optimized Escalation**: Зөвхөн үнэхээр шаардлагатай үед л дэмжлэгийн багт мэдээлэх

## Суулгалт

1. Dependencies суулгах:

```bash
pip install -r requirements.txt
```

2. Environment хувьсагчдыг тохируулах (.env файл үүсгэх):

```bash
# OpenAI тохиргоо
OPENAI_API_KEY=your-openai-api-key
ASSISTANT_ID=your-assistant-id

# Chatwoot тохиргоо
CHATWOOT_API_KEY=your-chatwoot-api-key
ACCOUNT_ID=your-account-id

# Email тохиргоо
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-app-password

# Teams тохиргоо (заавал биш)
TEAMS_WEBHOOK_URL=your-teams-webhook-url

# JWT тохиргоо
JWT_SECRET=your-secret-key-here
VERIFICATION_URL_BASE=http://localhost:5000

# RAG системийн тохиргоо
RAG_ENABLED=true
CRAWL_MAX_PAGES=100
ESCALATION_THRESHOLD=3
```

3. Апп ажиллуулах:

```bash
python main.py
```

## RAG Системийн Ашиглалт

### 1. Vector Store үүсгэх

```bash
curl -X POST http://localhost:5000/rag/build
```

### 2. RAG статус шалгах

```bash
curl http://localhost:5000/rag/status
```

### 3. CloudMN docs-аас хайлт хийх (тест)

```bash
curl -X POST http://localhost:5000/rag/search \
  -H "Content-Type: application/json" \
  -d '{"query": "сервер үүсгэх", "k": 5}'
```

### 4. Cache цэвэрлэж шинээр crawl хийх

```bash
curl -X POST http://localhost:5000/rag/refresh
```

### 5. Асуултын логик тест хийх

```bash
curl -X POST http://localhost:5000/rag/test-query \
  -H "Content-Type: application/json" \
  -d '{"conv_id": "test_123", "query": "сервер үүсгэх"}'
```

### 6. Асуултын түүх цэвэрлэх

```bash
# Бүх conversation-ы түүх цэвэрлэх
curl -X POST http://localhost:5000/rag/clear-history \
  -H "Content-Type: application/json" \
  -d '{"conv_id": "all"}'

# Тодорхой conversation цэвэрлэх
curl -X POST http://localhost:5000/rag/clear-history \
  -H "Content-Type: application/json" \
  -d '{"conv_id": "specific_conv_id"}'
```

## API Endpoints

- `POST /webhook` - Chatwoot webhook
- `GET /verify` - Имэйл баталгаажуулалт
- `GET /rag/status` - RAG систем статус
- `POST /rag/build` - Vector store үүсгэх
- `POST /rag/search` - RAG хайлт (тест)
- `POST /rag/refresh` - Cache цэвэрлэж шинэчлэх
- `POST /rag/test-query` - Асуултын логик тест хийх
- `POST /rag/clear-history` - Асуултын түүх цэвэрлэх
- `GET /test-teams` - Teams webhook тест
- `GET /debug-env` - Environment хувьсагч статус

## Ажиллагааны дараалал

1. **Хэрэглэгч мессеж илгээх** → Chatwoot webhook
2. **Имэйл шалгах** → Хэрэв баталгаажуулаагүй бол имэйл шаардах
3. **RAG хайлт** → CloudMN docs-аас холбогдох мэдээлэл олох
4. **AI хариулт** → OpenAI Assistant + RAG мэдээллээр хариулт үүсгэх
5. **Teams мэдээлэл** → Шаардлагатай үед техникийн багт илгээх

## RAG Системийн Ухаалаг Логик

### Асуултын Төрлийн Танилт

- **Анхны асуулт**: Шинэ conversation эхлэх үед - RAG хайлт хийж URL олгоно
- **Ижил төрлийн асуулт**: Өмнөх асуулттай ижил төрлийн техникийн асуудал - өмнөх URL ашиглана
- **Шинэ төрлийн асуулт**: Өөр төрлийн асуудал - шинэ RAG хайлт хийж шинэ URL олгоно

### URL Менежмент

- Conversation бүрт сүүлд олдсон URL хуудсуудыг хадгална
- Ижил төрлийн асуултанд дахин хайлт хийхгүйгээр өмнөх URL-үүдийг санал болгоно
- Шинэ асуудал орж ирвэл шинэ URL хайж, хуучин URL-ыг солино

### Дэмжлэгийн Багт Мэдээлэх Шалгуур

- **Асуултын тоо**: `ESCALATION_THRESHOLD` (анхдагч: 3) хүрвэл мэдээлэх
- **Яаралтай түлхүүр үгс**: "алдаа гарч байна", "тусламж хэрэгтэй" гэх мэт
- **AI алдаа**: Олон удаа дараалан AI алдаа гарвал автоматаар мэдээлэх
- **RAG идэвхгүй**: RAG систем ажиллахгүй бол мэдээлэх

## Файлын бүтэц

- `main.py` - Гол апп файл
- `requirements.txt` - Python dependencies
- `cloudmn_crawl_cache.json` - Crawl хийсэн мэдээллийн cache
- `cloudmn_vectorstore.faiss` - FAISS vector database
- `cloudmn_vectorstore.pkl` - Vector store metadata

## Хөгжүүлэлтийн тэмдэглэл

- Cache систем ашиглан давтан crawl хийхээс зайлсхийх
- Vector store нэг удаа үүсгэж, дараа нь memory-д ачаалах
- Teams integration-ыг асуудлын дүгнэлтэд ашиглах
- Retry logic AI алдаа гарвал дахин оролдох
- **Шинэ**: Асуултын төрлийг GPT-ээр танин ижил асуудалд давтан хайлт хийхгүй байх
- **Шинэ**: URL хадгалалт ба conversation-ны түүх менежмент
- **Шинэ**: Escalation логикийг хязгаарлаж зөвхөн шаардлагатай үед Teams-д мэдээлэх

## Тестлэх заавар

### 1. RAG логик тест

```bash
# Анхны асуулт - шинэ хайлт хийх ёстой
curl -X POST http://localhost:5000/rag/test-query \
  -d '{"conv_id": "test", "query": "сервер үүсгэх"}'

# Ижил төрлийн асуулт - хайлт хийхгүй
curl -X POST http://localhost:5000/rag/test-query \
  -d '{"conv_id": "test", "query": "серверийг хэрхэн бэлдэх вэ"}'

# Өөр төрлийн асуулт - шинэ хайлт хийх
curl -X POST http://localhost:5000/rag/test-query \
  -d '{"conv_id": "test", "query": "домайн нэр бүртгэх"}'
```

### 2. Escalation тест

```bash
# Олон асуулт гаргаж escalation threshold хүрүүлэх
for i in {1..4}; do
  curl -X POST http://localhost:5000/rag/test-query \
    -d "{\"conv_id\": \"escalation_test\", \"query\": \"асуулт $i\"}"
done
```

## Алдаа засалт

- `RAG систем идэвхгүй` - `RAG_ENABLED=true` тохируулна уу
- `Vector store олдсонгүй` - `/rag/build` дуудаж эхлээд үүсгэна үу
- `Crawl алдаа` - Интернет холболт, docs.cloud.mn хүртээмжтэй эсэхийг шалгана уу
- `Ижил асуулт танихгүй` - GPT-3.5-turbo холболт шалгана уу
- `Teams мэдээлэх алдаа` - `TEAMS_WEBHOOK_URL` зөв тохируулсан эсэхийг шалгана уу
