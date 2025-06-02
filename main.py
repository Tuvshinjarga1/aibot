import os
import requests
import re
import jwt
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string

# ── Load .env ───────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── Logging ─────────────────────────────────────────────────────────────────────
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Environment variables ────────────────────────────────────────────────────────
CHATWOOT_API_KEY      = os.getenv("CHATWOOT_API_KEY", "").strip()
ACCOUNT_ID            = os.getenv("ACCOUNT_ID", "").strip()
# API Channel ашиглахгүй тул INBOX_ID-ыг ашиглахгүй

SENDER_EMAIL          = os.getenv("SENDER_EMAIL", "").strip()
SENDER_PASSWORD       = os.getenv("SENDER_PASSWORD", "").strip()
SMTP_SERVER           = os.getenv("SMTP_SERVER", "smtp.gmail.com").strip()
SMTP_PORT             = int(os.getenv("SMTP_PORT", "587"))

VERIFICATION_URL_BASE = os.getenv("VERIFICATION_URL_BASE", "http://localhost:5000").strip()
JWT_SECRET            = os.getenv("JWT_SECRET", "your-secret-key-here").strip()

# Microsoft Teams webhook (заавал биш)
TEAMS_WEBHOOK_URL     = os.getenv("TEAMS_WEBHOOK_URL", "").strip()


# ── Помощник функций ─────────────────────────────────────────────────────────────

def is_valid_email(email: str) -> bool:
    """Имэйл хаяг зөв эсэх шалгах."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def generate_verification_token(email: str, conv_id: int, contact_id: int) -> str:
    """JWT токен үүсгэх (24 цагийн хүчинтэй)."""
    payload = {
        'email': email,
        'conv_id': conv_id,
        'contact_id': contact_id,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token: str):
    """JWT токен шалгах."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

def send_verification_email(email: str, token: str) -> bool:
    """
    Баталгаажуулах линк рүү имэйл илгээх.
    /verify?token=<JWT> хэлбэртэй URL болно.
    """
    try:
        verification_url = f"{VERIFICATION_URL_BASE}/verify?token={token}"
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = email
        msg['Subject'] = "Имэйл хаягаа баталгаажуулна уу"

        body = f"""
Сайн байна уу!

Таны имэйл хаягийг баталгаажуулахын тулд доорх линк дээр дарна уу:

{verification_url}

Энэ линк 24 цагийн дараа хүчин төгөлдөргүй болно.

Хэрвээ та биш бол бидэнд мэдэгдэнэ үү.

Баярлалаа!
"""
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        logger.error(f"Имэйл илгээхэд алдаа: {e}")
        return False

def get_contact(contact_id: int) -> dict:
    """Contact мэдээллийг Chatwoot-аас авах."""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()

def update_contact(contact_id: int, attrs: dict) -> dict:
    """Contact-ийн custom_attributes шинэчлэх."""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"custom_attributes": attrs}
    resp = requests.put(url, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()

def send_to_chatwoot(conv_id: int, text: str) -> None:
    """Chatwoot руу outgoing (агент) мессеж илгээх."""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "content": text,
        "message_type": "outgoing",
        "private": False
    }
    logger.info(f"📤 Sending to Chatwoot: {url}  payload={payload}")
    resp = requests.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    logger.info(f"📥 Chatwoot response: {resp.status_code}")

def send_teams_notification(conv_id: int, customer_message: str, customer_email: str = None,
                            escalation_reason: str = "Хэрэглэгчийн асуудал", ai_analysis: str = None) -> bool:
    """Microsoft Teams рүү техникийн мэдээлэл илгээх (заавал биш)."""
    if not TEAMS_WEBHOOK_URL:
        return False
    try:
        conv_url = f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
        error_summary = escalation_reason
        if ai_analysis:
            error_summary += f"\n\nAI анализ: {ai_analysis}"
        teams_payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.3",
                    "body": [
                        {"type": "TextBlock", "text": "📋 Хэрэглэгчийн дүгнэлт", "weight": "Bolder", "size": "Medium", "color": "Attention"},
                        {"type": "TextBlock", "text": "AI дүн шинжилгээ хийгдэж байна.", "wrap": True},
                        {"type": "FactSet", "facts": [
                            {"title": "Харилцагч:", "value": customer_email or "Тодорхойгүй"},
                            {"title": "Мессеж:", "value": customer_message[:300] + ("..." if len(customer_message) > 300 else "")},
                            {"title": "Цаг:", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                        ]}
                    ]
                }
            }]
        }
        if ai_analysis:
            teams_payload["attachments"][0]["content"]["body"].append({
                "type": "TextBlock", "text": "🤖 AI дүгнэлт:", "weight": "Bolder", "size": "Medium", "spacing": "Large"
            })
            teams_payload["attachments"][0]["content"]["body"].append({
                "type": "TextBlock", "text": ai_analysis, "wrap": True, "fontType": "Monospace", "color": "Good"
            })
        teams_payload["attachments"][0]["content"]["actions"] = [
            {"type": "Action.OpenUrl", "title": "Chatwoot дээр харах", "url": conv_url}
        ]
        resp = requests.post(TEAMS_WEBHOOK_URL, json=teams_payload)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Teams мэдээлэл илгээхэд алдаа: {e}")
        return False


# ── Flask Routes ────────────────────────────────────────────────────────────────

@app.route("/verify", methods=["GET"])
def verify_email():
    """
    /verify?token=<JWT> endpoint:
    - Баталгаажуулах линкээр хандсан тохиолдолд токений хүчинтэйг шалгаж,
      тухайн контакт дээр email_verified=true болгож тэмдэглэнэ.
    - Мөн Chatwoot руу “имэйл баталгаажлаа” гэсэн outgoing мессеж илгээнэ.
    """
    token = request.args.get('token')
    if not token:
        return "Токен олдсонгүй!", 400

    payload = verify_token(token)
    if not payload:
        return "Токен хүчин төгөлдөр бус эсвэл хүчинтэй хугацаа дууссан!", 400

    try:
        conv_id    = payload['conv_id']
        contact_id = payload['contact_id']
        email      = payload['email']

        # Contact дээр баталгаажуулалтын тэмдэг нэмэх
        update_contact(contact_id, {
            "email_verified": "true",
            "verified_email": email,
            "verification_date": datetime.utcnow().isoformat()
        })

        # Chatwoot руу мэдэгдэх
        send_to_chatwoot(conv_id, f"✅ Таны имэйл хаяг ({email}) баталгаажлаа! Одоо бот-тэй харилцаж болно.")

        # Амжилттай баталгаажуулсны дараах HTML хуудас
        return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>Имэйл баталгаажлаа</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        .success { color: #4CAF50; font-size: 28px; margin: 20px 0; font-weight: bold; }
        .info { font-size: 18px; color: #333; }
        .email { font-weight: bold; color: #fff; background: rgba(0,0,0,0.1); padding: 8px; border-radius: 5px; }
    </style>
</head>
<body>
    <div class="success">🎉 Имэйл баталгаажуулах амжилттай!</div>
    <div class="info">
        Баталгаажих имэйл хаяг:<br>
        <div class="email">{{ email }}</div>
    </div>
    <div class="info">
        ✅ Одоо та бот-тэй харилцаж болно!<br>
        🤖 Chatwoot чат цонх руу буцаж асуултаа бичнэ үү.
    </div>
</body>
</html>
""", email=email)

    except Exception as e:
        logger.error(f"Verification алдаа: {e}")
        return "Баталгаажуулахад алдаа гарлаа!", 500


@app.route("/webhook", methods=["POST"])
def webhook_handler():
    """
    Chatwoot webhook handler:
    - Зөвхөн "incoming" мессежэд хариу үйлдэл үзүүлнэ.
    - Баталгаажуулаагүй бол: "имэйл хаягаа илгээ" → токен үүсгэн илгээх.
    - Баталгаажсан бол: энгийн echo-бот маягаар reply илгээх.
    """
    try:
        data = request.json or {}
        logger.info(f"🔄 Webhook ирлээ: {data.get('message_type', 'unknown')}")

        # Зөвхөн “incoming” мессеж
        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped - not incoming"}), 200

        # 1) conv_id болон message_content
        conv_id = data.get("conversation", {}).get("id")
        message_content = (data.get("content") or "").strip()
        logger.info(f"📝 conv_id={conv_id}, content='{message_content}'")

        # 2) contact_id
        contact_id = None
        if data.get("sender") and data["sender"].get("id"):
            contact_id = data["sender"]["id"]

        # Хэрвээ contact_id байхгүй бол → энд бид шинэ contact үүсгэхгүй,
        # утга нь Chatwoot Inbox дээр ямар нэгэн контакттай холбогдсон гэж үзнэ.
        if not contact_id:
            # Шинэ контактгүй бол шууд “имэйлээ бич” гэж хэлэх
            send_to_chatwoot(conv_id,
                "👋 Сайн байна уу! Chatbot ашиглахын тулд эхлээд имэйл хаягаа баталгаажуулна уу.\n"
                "📧 Зөв жишээ: example@gmail.com")
            return jsonify({"status": "waiting_for_email"}), 200

        # 3) Баталгаажуулалтын статус шалгах
        is_verified = False
        verified_email = ""

        # 3.1 Webhook JSON дотор contact custom_attributes дунд байгаа эсэх
        if "conversation" in data and "meta" in data["conversation"] and "sender" in data["conversation"]["meta"]:
            attrs = data["conversation"]["meta"]["sender"].get("custom_attributes", {})
            email_verified_value = attrs.get("email_verified", "")
            verified_email = attrs.get("verified_email", "")
            is_verified = str(email_verified_value).lower() in ["true", "1", "yes"]
            logger.info(f"Webhook-ээс баталгаажуулсан: {is_verified}, verified_email={verified_email}")

        # 3.2 Хэрвээ webhook JSON-д байхгүй бол API-аар дахин шалгах
        if not is_verified:
            try:
                contact = get_contact(contact_id)
                attrs = contact.get("custom_attributes", {})
                email_verified_value = attrs.get("email_verified", "")
                verified_email = attrs.get("verified_email", "")
                is_verified = str(email_verified_value).lower() in ["true", "1", "yes"]
                logger.info(f"API-аас баталгаажуулсан: {is_verified}, verified_email={verified_email}")
            except Exception as e:
                logger.error(f"❌ Contact авах алдаа: {e}")
                is_verified = False

        # 4) Хэрвээ баталгаажуулаагүй бол:
        if not is_verified:
            logger.info("🚫 Баталгаажуулалтгүй – имэйл шаардлагатай.")
            # 4.1 Хэрвээ ирсэн content нь email маягтай бол токен үүсгэн имэйл явуулах
            if is_valid_email(message_content):
                token = generate_verification_token(message_content, conv_id, contact_id)
                if send_verification_email(message_content, token):
                    send_to_chatwoot(conv_id,
                        f"📧 Таны имэйл ({message_content}) рүү баталгаажуулах линк илгээлээ.\n"
                        "Имэйлээ шалгаж, линк дээр дарна уу. Линк 24 цагийн дараа хүчин төгөлдөргүй болно.\n"
                        "⚠️ Spam фолдерыг шалгахаа мартуузай!")
                    logger.info("✅ Баталгаажуулах имэйл илгээлээ.")
                else:
                    send_to_chatwoot(conv_id, "❌ Имэйл илгээхэд алдаа гарлаа. Дахин оролдоно уу.")
                    logger.error("❌ Баталгаажуулах имэйл илгээхэд алдаа.")
            else:
                # 4.2 Хэрвээ email маяггүй текст ирсэн бол зөв имэйл хүсэх
                send_to_chatwoot(conv_id,
                    "👋 Сайн байна уу! Chatbot ашиглахын тулд эхлээд зөв имэйл хаягаа бичээд илгээнэ үү.\n"
                    "📧 Жишээ: example@gmail.com")
            return jsonify({"status": "waiting_for_verification"}), 200

        # 5) Баталгаажуулсан контакт дээр энгийн “echo” маягаар хариу илгээх
        reply_text = f"🤖 Бот хариулт: \"{message_content}\""
        send_to_chatwoot(conv_id, reply_text)
        logger.info(f"✅ Баталгаажагдсан контакт дээр хариу илгээлээ: {reply_text}")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

if __name__ == "__main__":
    # Debug=True бол алдаа гарсан үед дэлгэрэнгүй мэдээлэл гаргана
    app.run(host="0.0.0.0", port=5000, debug=True)
