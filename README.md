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

# Chatwoot AI Chatbot with Microsoft Teams Integration

Flask дээр бичигдсэн Chatwoot webhook handler бөгөөд OpenAI Assistant API ашиглан AI chatbot ажиллуулдаг. Microsoft Teams-тэй холбогдож AI хариулт олдохгүй үед ажилтанд автомат мэдээлдэг.

## Боломжууд

- ✅ OpenAI Assistant API ашиглан AI chatbot
- ✅ Email баталгаажуулалт (JWT токен)
- ✅ Chatwoot API холболт
- ✅ Microsoft Teams мэдээлэх систем
- ✅ Keyword-ээр ажилтан дуудах
- ✅ AI алдаа гарсан үед автомат escalation
- ✅ Retry logic AI-д

## Microsoft Teams тохируулах

### 1. Teams Webhook URL үүсгэх

1. Microsoft Teams дээр team/channel сонгох
2. Channel дээр **"Connectors"** товч дарах
3. **"Incoming Webhook"** хайж олох
4. **"Configure"** дарах
5. Webhook-д нэр өгөх (жнь: "Chatwoot Notifications")
6. **"Create"** дарах
7. Үүссэн webhook URL-г хуулж авах

### 2. Environment Variable тохируулах

`.env` файлдаа Teams webhook URL нэмэх:

```
TEAMS_WEBHOOK_URL=https://your-org.webhook.office.com/webhookb2/...
```

### 3. Ажилтан дуудах keyword-үүд

Хэрэглэгч дараах үгсийг хэрэглэвэл автоматаар ажилтанд хуваарилна:

- "ажилтан"
- "хүн"
- "дуудаад өг"
- "холбоод өг"
- "тусламж"
- "туслаач"
- "ярилцмаар"
- "manager"
- "supervisor"

### 4. AI алдаа гарсан үед

Дараах тохиолдолд Teams-ээр ажилтанд мэдээлнэ:

- AI 3 удаа дараалан алдаа гаргавал
- AI timeout болвол
- AI хариулт олдохгүй бол
- OpenAI API алдаа гарвал

## Суулгах заавар

### 1. Dependencies суулгах

```bash
pip install -r requirements.txt
```

### 2. Environment variables тохируулах

```bash
cp env_example.txt .env
# .env файлыг засаж бүх утгуудыг оруулах
```

### 3. Ажиллуулах

```bash
python main.py
```

## Teams мэдээллийн жишээ

AI асуудал гарсан үед Teams дээр ийм мэдээлэл ирнэ:

```
🚨 Хэрэглэгч ажилтантай холбогдохыг хүсч байна

Шалтгаан: AI хариулт олдсонгүй
Харилцагч: customer@email.com
Мессеж: Би ажилтантай ярилцмаар байна...
Хугацаа: 2024-01-15 14:30:00

[Chatwoot дээр харах] товч
```

## Тохиргоо

`main.py` файлд дараах тохиргоонуудыг өөрчлөх боломжтой:

```python
# Ажилтан дуудах keyword-үүд
ESCALATE_TO_HUMAN_KEYWORDS = ["ажилтан", "хүн", "дуудаад өг", ...]

# AI хэдэн удаа оролдсоны дараа ажилтанд хуваарилах
MAX_AI_RETRIES = 2
```
