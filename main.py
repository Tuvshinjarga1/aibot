import os
import time
import requests
import re
import jwt
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
from openai import OpenAI
from dotenv import load_dotenv
import logging

# Load .env
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ─── Орчны хувьсагчид ──────────────────────────────────────────────────────────
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID      = os.environ["ASSISTANT_ID"]
CHATWOOT_API_KEY  = os.environ["CHATWOOT_API_KEY"]
ACCOUNT_ID        = os.environ["ACCOUNT_ID"]
INBOX_ID          = os.environ["INBOX_ID"]            # Chatwoot-д үүсгэсэн API Channel Inbox ID
CHATWOOT_BASE_URL = os.environ.get("CHATWOOT_BASE_URL", "https://app.chatwoot.com")

SMTP_SERVER       = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT         = int(os.environ.get("SMTP_PORT", "587"))
SENDER_EMAIL      = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD   = os.environ["SENDER_PASSWORD"]

TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")
MAX_AI_RETRIES    = 2

JWT_SECRET        = os.environ.get("JWT_SECRET", "your-secret-key-here")
VERIFICATION_URL_BASE = os.environ.get("VERIFICATION_URL_BASE", "http://localhost:5000")

# OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY)


# ─── Chatwoot ҮЙЛДЭЛҮҮД ──────────────────────────────────────────────────────────

def is_valid_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def generate_verification_token(email: str, conv_id: int, contact_id: int) -> str:
    payload = {
        'email': email,
        'conv_id': conv_id,
        'contact_id': contact_id,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def verify_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def send_verification_email(email: str, token: str) -> bool:
    try:
        verification_url = f"{VERIFICATION_URL_BASE}/verify?token={token}"
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = email
        msg['Subject'] = "Имэйл хаягаа баталгаажуулна уу"

        body = f"""
Сайн байна уу!

Таны имэйл хаягийг баталгаажихын тулд доорх линк дээр дарна уу:

{verification_url}

Энэ линк 24 цагийн дараа хүчингүй болно.

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
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()


def create_or_update_contact(email: str, name: str = None) -> int:
    # Эхлээд имэйлээр хайж, байвал update, үгүй бол create
    search_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/search"
    resp = requests.get(search_url, params={"q": email}, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    payload = resp.json().get("payload", [])
    if payload:
        existing = payload[0]
        cid = existing["id"]
        # Custom attribute-д email_verified=1 байршуулж болно дараа update-д ашиглана
        update_contact(cid, {"verified_email": email})
        return cid

    # Шинэ контакт үүсгэх
    create_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts"
    contact_data = {
        "name": name or email.split("@")[0],
        "email": email,
    }
    headers = {"api_access_token": CHATWOOT_API_KEY, "Content-Type": "application/json"}
    resp = requests.post(create_url, json=contact_data, headers=headers)
    resp.raise_for_status()
    new_contact = resp.json()["payload"]["contact"]
    return new_contact["id"]


def update_contact(contact_id: int, attrs: dict) -> dict:
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    payload = {"custom_attributes": attrs}
    resp = requests.put(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()


def create_conversation(contact_id: int) -> int:
    # Contact-ийн inbox-д холбогдоогүй бол эхлээд inbox-д холбох entry (source_id) хэрэгтэй
    # Ихэнхдээ contact["meta"]["sender"]["inboxes"][0]["source_id"] -г ашиглана.
    # Гэхдээ бид API Channel -> inbox_id ашиглана.
    inbox_id = INBOX_ID
    # Зарим хувилбарт "source_id" шаардлагатай байж болно. Алдаа гарвал Chatwoot-д харж аваарай.
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    payload = {
        "contact_id": contact_id,
        "inbox_id": inbox_id
    }
    headers = {"api_access_token": CHATWOOT_API_KEY, "Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    conv = resp.json()["payload"]["conversation"]
    return conv["id"]


def update_conversation(conv_id: int, attrs: dict) -> dict:
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes"
    payload = {"custom_attributes": attrs}
    resp = requests.post(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()


def send_to_chatwoot(conv_id: int, text: str) -> None:
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {"api_access_token": CHATWOOT_API_KEY, "Content-Type": "application/json"}
    payload = {
        "content": text,
        "message_type": "outgoing",
        "private": False
    }
    r = requests.post(url, json=payload, headers=headers)
    r.raise_for_status()


# ─── AI ASSISTANT ФУНКЦУУД ──────────────────────────────────────────────────────

def clean_ai_response(response: str) -> str:
    try:
        import json
        if response.strip().startswith('{') and response.strip().endswith('}'):
            try:
                data = json.loads(response)
                if isinstance(data, dict) and ("email" in data or "issue" in data):
                    return "Таны хүсэлтийг техникийн багт дамжууллаа. Удахгүй хариу өгнө."
            except json.JSONDecodeError:
                pass
        json_pattern = r'\{[^}]*"email"[^}]*\}'
        response = re.sub(json_pattern, '', response)
        response = re.sub(r'\n\s*\n', '\n', response).strip()
        if len(response) < 20:
            return "Таны хүсэлтийг хүлээн авлаа. Удахгүй хариу өгнө."
        return response
    except Exception as e:
        logger.error(f"AI хариулт цэвэрлэхэд алдаа: {e}")
        return response


def get_ai_response(thread_id: str, message_content: str, conv_id: int = None,
                    customer_email: str = None, retry_count: int = 0) -> str:
    try:
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=message_content)
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)

        max_wait = 30
        wait_count = 0
        while wait_count < max_wait:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status == "completed":
                break
            elif run_status.status in ["failed", "cancelled", "expired"]:
                error_msg = "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."
                if retry_count == 0 and conv_id:
                    send_teams_notification(conv_id, message_content, customer_email,
                                            f"AI run статус алдаа: {run_status.status}",
                                            f"OpenAI run ID: {run.id}, Статус: {run_status.status}")
                return error_msg
            time.sleep(1)
            wait_count += 1

        if wait_count >= max_wait:
            timeout_msg = "Хариулахад хэт удаж байна. Дахин оролдоно уу."
            if retry_count == 0 and conv_id:
                send_teams_notification(conv_id, message_content, customer_email,
                                        "AI хариулт timeout (30 секунд)",
                                        f"OpenAI run ID: {run.id}, Thread ID: {thread_id}")
            return timeout_msg

        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in messages.data:
            if msg.role == "assistant":
                reply = "".join([b.text.value for b in msg.content if hasattr(b, 'text')])
                return clean_ai_response(reply)

        no_response_msg = "Хариулт олдсонгүй. Дахин оролдоно уу."
        if retry_count == 0 and conv_id:
            send_teams_notification(conv_id, message_content, customer_email,
                                    "AI хариулт олдсонгүй", f"Thread ID: {thread_id}")
        return no_response_msg

    except Exception as e:
        logger.error(f"AI хариулт авахад алдаа: {e}")
        error_msg = "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."
        if retry_count == 0 and conv_id:
            send_teams_notification(conv_id, message_content, customer_email,
                                    "AI системийн алдаа (Exception)", f"Exception: {e}")
        return error_msg


def send_teams_notification(conv_id: int, customer_message: str, customer_email: str = None,
                            escalation_reason: str = "Хэрэглэгчийн асуудал", ai_analysis: str = None) -> bool:
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


# ─── Flask Routes ───────────────────────────────────────────────────────────────

@app.route("/verify", methods=["GET"])
def verify_email():
    token = request.args.get('token')
    if not token:
        return "Токен олдсонгүй!", 400

    payload = verify_token(token)
    if not payload:
        return "Токен хүчинтэй бус эсвэл хугацаа дууссан!", 400

    try:
        conv_id = payload['conv_id']
        contact_id = payload['contact_id']
        email = payload['email']

        update_contact(contact_id, {
            "email_verified": "1",
            "verified_email": email,
            "verification_date": datetime.utcnow().isoformat()
        })

        thread_key = f"openai_thread_{contact_id}"
        update_conversation(conv_id, {thread_key: None})

        send_to_chatwoot(conv_id, f"✅ Таны имэйл хаяг ({email}) баталгаажлаа! Одоо та chatbot-тай харилцаж болно.")

        return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>Имэйл баталгаажлаа</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        .success { color: green; font-size: 24px; margin: 20px 0; }
        .info { color: #666; font-size: 16px; }
    </style>
</head>
<body>
    <div class="success">✅ Баталгаажуулалт амжилттай!</div>
    <div class="info">Таны имэйл ({{ email }}) баталгаажлаа.<br>Одоо та chatbot-тай харилцаж болно.</div>
</body>
</html>
""", email=email)

    except Exception as e:
        logger.error(f"Verification алдаа: {e}")
        return "Баталгаажуулахад алдаа гарлаа!", 500


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        logger.info(f"🔄 Webhook ирлээ: {data.get('message_type', 'unknown')}")

        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped - not incoming"}), 200

        conv_id = data["conversation"]["id"]
        message_content = data.get("content", "").strip()

        logger.info(f"📝 Conversation ID: {conv_id}, Хэрэглэгчийн мессеж: '{message_content}'")

        # Contact ID олох
        contact_id = None
        if "sender" in data and data["sender"]:
            contact_id = data["sender"].get("id")

        # Хэрвээ contact_id байхгүй бол шинэ Contact үүсгэнэ
        if not contact_id:
            # Хэрэглэгчийн имэйл эсвэл нэр байхгүй учир /contacts API дуудлага хэрэгтэй
            # Жишээ: “message_content”-д шууд имэйл бичигдсэн бол:
            if is_valid_email(message_content):
                new_contact_id = create_or_update_contact(message_content)
                contact_id = new_contact_id
                # Шинэ Conversation үүсгэх
                conv_id = create_conversation(contact_id)
            else:
                # Имэйл биш мессеж ирсэн бол түр хариулт өгөх
                send_to_chatwoot(conv_id,
                    "👋 Сайн байна уу! Chatbot ашиглахын тулд эхлээд имэйл хаягаа баталгаажуулна уу.\n"
                    "📧 Жишээ: example@gmail.com")
                return jsonify({"status": "waiting_verification"}), 200
        else:
            # Хэрэв webhook JSON-д conversation ID ирээгүй бол create_conversation
            if not conv_id:
                conv_id = create_conversation(contact_id)

        logger.info(f"👤 Contact ID: {contact_id}, Conversation ID: {conv_id}")

        # ───── Баталгаажуулалт шалгах ────────────────────────────────────────────
        is_verified = False
        verified_email = ""

        if "conversation" in data and "meta" in data["conversation"] and "sender" in data["conversation"]["meta"]:
            attrs = data["conversation"]["meta"]["sender"].get("custom_attributes", {})
            email_verified_value = attrs.get("email_verified", "")
            verified_email = attrs.get("verified_email", "")
            is_verified = str(email_verified_value).lower() in ["true", "1", "yes"]
            logger.info(f"Webhook-ээс баталгаажуулсан: {is_verified}, verified_email={verified_email}")

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

        if not is_verified:
            logger.info("🚫 Баталгаажуулалтгүй - имэйл шаардаж байна")
            if is_valid_email(message_content):
                token = generate_verification_token(message_content, conv_id, contact_id)
                if send_verification_email(message_content, token):
                    send_to_chatwoot(conv_id,
                        f"📧 Таны имэйл ({message_content}) рүү баталгаажуулах линк илгээлээ.\n"
                        "Линк 24h дараа хүчин төгөлдөргүй болно.\n"
                        "⚠️ Spam фолдерыг шалгахаа мартуузай!")
                    logger.info("✅ Имэйл илгээлээ")
                else:
                    send_to_chatwoot(conv_id, "❌ Имэйл илгээхэд алдаа гарлаа.")
                    logger.error("❌ Имэйл илгээх алдаа")
            else:
                send_to_chatwoot(conv_id,
                    "👋 Сайн байна уу! Chatbot ашиглахын тулд эхлээд имэйл хаягаа баталгаажуулна уу.\n"
                    "📧 Жишээ: example@gmail.com")
            return jsonify({"status": "waiting_verification"}), 200

        # ─── AI ASSISTANT ХАРИЛЦАА ────────────────────────────────────────────────
        logger.info("🤖 AI Assistant ажиллаж байна...")

        conv = get_conversation(conv_id)
        conv_attrs = conv.get("custom_attributes", {})
        thread_key = f"openai_thread_{contact_id}"
        thread_id = conv_attrs.get(thread_key)
        if not thread_id:
            logger.info("🧵 Шинэ thread үүсгэж байна...")
            thread = client.beta.threads.create()
            thread_id = thread.id
            update_conversation(conv_id, {thread_key: thread_id})
            logger.info(f"✅ Thread үүсгэлээ: {thread_id}")
        else:
            logger.info(f"✅ Одоо байгаа thread ашиглаж байна: {thread_id}")

        ai_response_text = None
        ai_success = False

        def run_ai_assistant():
            nonlocal ai_response_text, ai_success
            try:
                retry_count = 0
                while retry_count <= MAX_AI_RETRIES:
                    resp = get_ai_response(thread_id, message_content, conv_id, verified_email, retry_count)
                    if not any(err in resp for err in ["алдаа гарлаа", "хэт удаж", "олдсонгүй"]):
                        ai_response_text = resp
                        ai_success = True
                        logger.info(f"✅ AI хариулт: {resp[:50]}...")
                        break
                    retry_count += 1
                    if retry_count <= MAX_AI_RETRIES:
                        logger.info(f"🔄 AI дахин оролдож байна... ({retry_count}/{MAX_AI_RETRIES})")
                        time.sleep(2)
                if not ai_success:
                    logger.error("❌ AI бүх оролдлого бүтэлгүйтэв")
            except Exception as e:
                logger.error(f"❌ AI алдаа: {e}")

        ai_thread = threading.Thread(target=run_ai_assistant)
        ai_thread.start()
        ai_thread.join(timeout=30)

        logger.info(f"🔍 AI амжилттай: {ai_success}")

        if ai_success:
            final_response = ai_response_text
            response_type = "AI Assistant"
        else:
            final_response = (
                "🚨 Уучлаарай, техникийн алдаа гарлаа. "
                "Таны асуултыг техникийн багт дамжуулсан. Удахгүй хариу өгнө."
            )
            response_type = "Error - Escalated"
            try:
                send_teams_notification(conv_id, message_content, verified_email,
                                        "AI Assistant хариулт алдаатай", None)
            except Exception as e:
                logger.error(f"❌ Teams мэдээлэх алдаа: {e}")

        send_to_chatwoot(conv_id, final_response)
        logger.info(f"✅ {response_type} хариулт илгээлээ: {final_response[:50]}...")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
