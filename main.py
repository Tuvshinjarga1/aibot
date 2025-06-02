import os
import time
import requests
import re
import jwt
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string

# ── .env-аас утгуудыг унших ───────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── Лог тохиргоо ─────────────────────────────────────────────────────────────────
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Орчны хувьсагчид ─────────────────────────────────────────────────────────────
CHATWOOT_API_KEY       = os.getenv("CHATWOOT_API_KEY", "").strip()
ACCOUNT_ID             = os.getenv("ACCOUNT_ID", "").strip()
INBOX_ID               = os.getenv("INBOX_ID", "").strip()
CHATWOOT_BASE_URL      = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com").rstrip("/")

# SMTP (имэйл илгээх) тохиргоо
SENDER_EMAIL           = os.getenv("SENDER_EMAIL", "").strip()
SENDER_PASSWORD        = os.getenv("SENDER_PASSWORD", "").strip()
SMTP_SERVER            = os.getenv("SMTP_SERVER", "smtp.gmail.com").strip()
SMTP_PORT              = int(os.getenv("SMTP_PORT", "587"))

# Баталгаажуулах токен болон линк үүсгэх тохиргоо
JWT_SECRET             = os.getenv("JWT_SECRET", "your-secret-key-here").strip()
VERIFICATION_URL_BASE  = os.getenv("VERIFICATION_URL_BASE", "http://localhost:5000").strip()

# Microsoft Teams webhook (заавал биш)
TEAMS_WEBHOOK_URL      = os.getenv("TEAMS_WEBHOOK_URL", "").strip()

# ── Туслах функцууд ──────────────────────────────────────────────────────────────

def is_valid_email(email: str) -> bool:
    """Имэйл хэлбэр зөв эсэхийг шалгах."""
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
    """JWT токеныг шалгах."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

def send_verification_email(email: str, token: str) -> bool:
    """
    Имэйл рүү баталгаажуулах линк илгээх.
    Баталгаажуулах линк нь /verify?token=<JWT> хэлбэртэй.
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
    """
    Contact-ийн custom_attributes шинэчлэх.
    Жишээ: {"email_verified": "true", "verified_email": "user@example.com"}
    """
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"custom_attributes": attrs}
    resp = requests.put(url, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()

def create_or_update_contact(email_or_name: str) -> int:
    """
    Хэрвээ имэйл гэж бичигдсэн бол тухайн Contact-ийг хайж, байвал ID-ийг буцаах.
    Байхгүй бол шинээр үүсгээд ID буцаах.
    Заавал имэйл биш бол name талбарт шууд бичнэ.
    """
    # 1) Имэйлээр хайж үзэх
    search_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/search"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    resp = requests.get(search_url, params={"q": email_or_name}, headers=headers)
    resp.raise_for_status()
    payload = resp.json().get("payload", [])
    if payload:
        existing = payload[0]
        return existing["id"]

    # 2) Шинээр үүсгэх
    create_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    is_email = is_valid_email(email_or_name)
    contact_data = {
        "name": email_or_name if not is_email else email_or_name.split("@")[0],
        "email": email_or_name if is_email else None
    }
    resp = requests.post(create_url, json=contact_data, headers=headers)
    resp.raise_for_status()
    new_contact = resp.json()["payload"]["contact"]
    return new_contact["id"]

def create_conversation(contact_id: int) -> int:
    """
    Шинэ Conversation үүсгэх (API Channel Inbox дээр).
    """
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "contact_id": contact_id,
        "inbox_id": INBOX_ID
    }
    resp = requests.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    conv = resp.json()["payload"]["conversation"]
    return conv["id"]

def send_to_chatwoot(conv_id: int, text: str) -> None:
    """
    Chatwoot руу outgoing (агент) мессеж илгээх.
    """
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
    """
    Microsoft Teams руу техникийн мэдээлэл илгээх (заавал биш).
    """
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
    Баталгаажуулах линкээр хандсан тохиолдолд token-ийн хүчинтэйг шалгаж,
    тухайн контакт дээр email_verified=true болгож тэмдэглэнэ.
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

        # Contact дээр баталгаажуулсан тэмдэг нэмэх
        update_contact(contact_id, {
            "email_verified": "true",
            "verified_email": email,
            "verification_date": datetime.utcnow().isoformat()
        })

        # Баталгаажсан pull request таны кодонд байхгүй тул эдгээрийг бичих шаардлагагүй 
        # send_to_chatwoot-р дамжуулан Chatwoot-д мэдэгдэх:
        send_to_chatwoot(conv_id, f"✅ Таны имэйл хаяг ({email}) баталгаажлаа! Одоо та AI BOT-тэй харьцаж болно.")

        # Амжилттай баталгаажуулсан талаарх HTML хуудас буцаах
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
    <div class="success">🎉 Имэйл баталгаажуулалт амжилттай!</div>
    <div class="info">
        Таны имэйл хаяг баталгаажлаа:<br>
        <div class="email">{{ email }}</div>
    </div>
    <div class="info">
        ✅ Одоо та AI BOT-тэй харилцаж болно!<br>
        🤖 Chatwoot-д очиж асуулт асуугаарай.
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
    - Хэрвээ мессеж ирвэл contact_id-ээ шалгаж, баталгаажсан эсэхийг харна.
    - Баталгаажуулаагүй бол эхлээд имэйл хаягаа баталгаажуулна уу гэж хэлнэ.
    - Хэрвээ имэйл маягтай үг ирэхээрэй бол баталгаажуулах линк явуулна.
    - Баталгаажуулсан contact дээр outgoing (ботын) мессеж илгээх.
    """
    try:
        data = request.json or {}
        logger.info(f"🔄 Webhook ирлээ: {data.get('message_type', 'unknown')}")

        # 1) Зөвхөн "incoming" мессежийг боловсруулна
        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped - not incoming"}), 200

        # 2) conversation ID болон мессежийн агуулга
        conv_id = data.get("conversation", {}).get("id")
        message_content = (data.get("content") or "").strip()
        logger.info(f"📝 conv_id={conv_id}, content='{message_content}'")

        # 3) Contact ID-г ол: 
        contact_id = None
        if data.get("sender") and data["sender"].get("id"):
            contact_id = data["sender"]["id"]

        # 4) Хэрвээ contact_id байхгүй бол шинээр үүсгэнэ
        if not contact_id:
            # Хэрвээ имэйл маягтай бол contact үүсгэнэ, үгүй бол dummy нэрээр үүсгэнэ.
            if is_valid_email(message_content):
                contact_id = create_or_update_contact(message_content)
            else:
                contact_id = create_or_update_contact("AnonymousUser")
            # Мөн шинэ conversation үүсгэх
            conv_id = create_conversation(contact_id)
            logger.info(f"👤 Шинэ Contact ID={contact_id}, Шинэ Conversation ID={conv_id}")
        else:
            # Хэрвээ conv_id авсангүй бол conversation үүсгэх
            if not conv_id:
                conv_id = create_conversation(contact_id)
                logger.info(f"👤 Missing conv_id, create new Conversation ID={conv_id}")

        # 5) Баталгаажуулалт шалгах
        is_verified = False
        verified_email = ""

        # 5.1 Webhook JSON-д яз contact custom_attributes дээр email_verified байгаа эсэх
        if "conversation" in data and "meta" in data["conversation"] and "sender" in data["conversation"]["meta"]:
            attrs = data["conversation"]["meta"]["sender"].get("custom_attributes", {})
            email_verified_value = attrs.get("email_verified", "")
            verified_email = attrs.get("verified_email", "")
            is_verified = str(email_verified_value).lower() in ["true", "1", "yes"]
            logger.info(f"Webhook-ээс баталгаажуулсан: {is_verified}, verified_email={verified_email}")

        # 5.2 Хэрвээ 5.1-д баталгаажуулалт байхгүй бол API-аар дахин шалгах
        if not is_verified:
            try:
                contact = get_contact(contact_id)
                attrs = contact.get("custom_attributes", {})
                email_verified_value = attrs.get("email_verified", "")
                verified_email = attrs.get("verified_email", "")
                is_verified = str(email_verified_value).lower() in ["true", "1", "yes"]
                logger.info(f"API-аас баталгаажуулсан: {is_verified}, verified_email={verified_email}")
            except Exception as e:
                logger.error(f"❌ Contact авахад алдаа: {e}")
                is_verified = False

        # 6) Хэрвээ баталгаажуулаагүй бол а) имэйл маягтай мессеж ирэх эсэхийг шалгах
        if not is_verified:
            logger.info("🚫 Баталгаажуулалтгүй, имэйл шаардлагатай")

            if is_valid_email(message_content):
                # Имэйл маягтай мессеж ирсэн үед баталгаажуулах линк илгээх
                token = generate_verification_token(message_content, conv_id, contact_id)
                if send_verification_email(message_content, token):
                    send_to_chatwoot(conv_id,
                        f"📧 Таны имэйл ({message_content}) рүү баталгаажуулах линк илгээлээ.\n"
                        "Имэйлээ шалгаад линк дээр дарна уу. Линк 24 цагийн дараа хүчин төгөлдөргүй болно.\n"
                        "⚠️ Spam фолдерыг шалгахаа мартуузай!")
                    logger.info("✅ Баталгаажуулах имэйл илгээлээ")
                else:
                    send_to_chatwoot(conv_id, "❌ Имэйл илгээхэд алдаа гарлаа. Дахин оролдоно уу.")
                    logger.error("❌ Баталгаажуулах имэйл илгээх алдаа")
            else:
                # Имэйл маяггүй болон бусад мессеж ирсэн бол зөв имэйл хүсэх
                send_to_chatwoot(conv_id,
                    "👋 Сайн байна уу! Chatbot ашиглахын тулд эхлээд имэйл хаягаа баталгаажуулна уу.\n"
                    "📧 Зөвхөн таны ашигладаг имэйл хаягийг яг бичиж илгээнэ үү.")
            return jsonify({"status": "waiting_verification"}), 200

        # 7) Баталгаажуулалт амжилттай бол outgoing мессежээр хариу илгээв
        reply_text = f"🤖 Таны бичсэн мессеж баталгаажиж, AI BOT-д илгээлээ: \"{message_content}\""
        send_to_chatwoot(conv_id, reply_text)
        logger.info(f"✅ Баталгаажсан контакт дээр хариу илгээв: {reply_text}")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
