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

# Орчны хувьсагчид
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID      = os.environ["ASSISTANT_ID"]
CHATWOOT_API_KEY  = os.environ["CHATWOOT_API_KEY"]
ACCOUNT_ID        = os.environ["ACCOUNT_ID"]
CHATWOOT_BASE_URL = "https://app.chatwoot.com"  # эсвэл өөрийн Chatwoot домэйн

# Email тохиргоо
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD = os.environ["SENDER_PASSWORD"]

# Microsoft Teams тохиргоо (заавал биш)
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")
MAX_AI_RETRIES = 2  # AI хэдэн удаа оролдсоны дараа ажилтанд хуваарилах

# JWT тохиргоо
JWT_SECRET = os.environ.get("JWT_SECRET", "your-secret-key-here")
VERIFICATION_URL_BASE = os.environ.get("VERIFICATION_URL_BASE", "http://localhost:5000")

# OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY)

# =============== CHATWOOT ФУНКЦУУД ===============

def is_valid_email(email):
    """Имэйл хаягийн форматыг шалгах"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def generate_verification_token(email, conv_id, contact_id):
    """JWT токен үүсгэх"""
    payload = {
        'email': email,
        'conv_id': conv_id,
        'contact_id': contact_id,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    """JWT токеныг шалгах"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def send_verification_email(email, token):
    """Имэйл илгээх"""
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

        Энэ линк 24 цагийн дараа хүчингүй болно.

        Хэрэв та биш бол бидэнд мэдэгдэнэ үү.

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

def get_contact(contact_id):
    """Contact мэдээлэл авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def update_contact(contact_id, attrs):
    """Contact-ийн custom attributes шинэчлэх"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    payload = {"custom_attributes": attrs}
    resp = requests.put(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def get_conversation(conv_id):
    """Conversation мэдээлэл авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def update_conversation(conv_id, attrs):
    """Conversation-ийн custom attributes шинэчлэх"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes"
    payload = {"custom_attributes": attrs}
    resp = requests.post(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def send_to_chatwoot(conv_id, text):
    """Chatwoot руу агентын (outgoing) мессеж илгээх"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {"api_access_token": CHATWOOT_API_KEY, "Content-Type": "application/json"}
    payload = {"content": text, "message_type": "outgoing", "private": False}
    r = requests.post(url, json=payload, headers=headers)
    r.raise_for_status()

def analyze_customer_issue(thread_id, current_message, customer_email=None):
    """AI ашиглан хэрэглэгчийн асуудлыг дүгнэж өгөх (GPT дүн шинжилгээ)"""
    try:
        # Сүүлийн 10 мессеж ав
        messages = client.beta.threads.messages.list(thread_id=thread_id, limit=10)
        conversation_history = []
        for msg in reversed(messages.data):
            if msg.role == "user":
                content = "".join([b.text.value for b in msg.content if hasattr(b, 'text')])
                if content.strip():
                    conversation_history.append(f"Хэрэглэгч: {content.strip()}")
            elif msg.role == "assistant":
                content = "".join([b.text.value for b in msg.content if hasattr(b, 'text')])
                if content.strip():
                    conversation_history.append(f"AI: {content.strip()[:100]}...")

        if not conversation_history:
            conversation_history = [f"Хэрэглэгч: {current_message}"]

        chat_history = "\n".join(conversation_history[-5:])
        system_msg = (
            "Та бол дэмжлэгийн мэргэжилтэн. "
            "Хэрэглэгчийн бүх чат түүхийг харж, асуудлыг иж бүрэн дүгнэж өгнө үү. "
            "Хэрэв олон асуудал байвал гол асуудлыг тодорхойлж фокуслана уу."
        )
        user_msg = f'''Хэрэглэгчийн чат түүх:
{chat_history}

Одоогийн мессеж: "{current_message}"

Дараах форматаар товч дүгнэлт өгнө үү:

АСУУДЛЫН ТӨРӨЛ: [Техникийн/Худалдааны/Мэдээллийн/Гомдол]
ЯАРАЛТАЙ БАЙДАЛ: [Өндөр/Дунд/Бага] 
ТОВЧ ТАЙЛБАР: [1 өгүүлбэрээр]
ШААРДЛАГАТАЙ АРГА ХЭМЖЭЭ: [Товч]'''
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            max_tokens=200,
            temperature=0.2,
            timeout=15
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"❌ Асуудал дүгнэхэд алдаа: {e}")
        return f"""АСУУДЛЫН ТӨРӨЛ: Тодорхойгүй
ЯАРАЛТАЙ БАЙДАЛ: Дунд
ТОВЧ ТАЙЛБАР: {current_message[:100]}
ШАARDЛАГАТАЙ АРГА ХЭМЖЭЭ: Ажилтны анхаарал шаардлагатай"""

def clean_ai_response(response: str) -> str:
    """AI хариултыг цэвэрлэх – JSON форматыг арилга"""
    try:
        import json
        if response.strip().startswith('{') and response.strip().endswith('}'):
            try:
                json_data = json.loads(response)
                if isinstance(json_data, dict) and ("email" in json_data or "issue" in json_data):
                    return "Таны хүсэлтийг техникийн дэмжлэгийн багт дамжуулаа. Удахгүй асуудлыг шийдэж, танд хариулт өгөх болно."
            except json.JSONDecodeError:
                pass
        json_pattern = r'\{[^}]*"email"[^}]*\}'
        response = re.sub(json_pattern, '', response)
        response = re.sub(r'\n\s*\n', '\n', response).strip()
        if len(response) < 20:
            return "Таны хүсэлтийг хүлээн авлаа. Удахгүй хариулт өгөх болно."
        return response
    except Exception as e:
        logger.error(f"❌ AI хариулт цэвэрлэхэд алдаа: {e}")
        return response

def get_ai_response(thread_id, message_content, conv_id=None, customer_email=None, retry_count=0):
    """OpenAI Assistant-ээс хариулт авах"""
    try:
        # Хэрэглэгчийн мессежийг thread-д нэмэх
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=message_content)
        # Assistant run үүсгэх
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
                                            f"OpenAI run ID: {run.id}, Status: {run_status.status}")
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

        # Үр дүнгээс assistant-ийн хариультыг авах
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in messages.data:
            if msg.role == "assistant":
                reply = "".join([b.text.value for b in msg.content if hasattr(b, 'text')])
                cleaned = clean_ai_response(reply)
                return cleaned

        no_response_msg = "Хариулт олдсонгүй. Дахин оролдоно уу."
        if retry_count == 0 and conv_id:
            send_teams_notification(conv_id, message_content, customer_email,
                                    "AI хариулт олдсонгүй", f"Thread ID: {thread_id}, Хариулт байхгүй")
        return no_response_msg
    except Exception as e:
        logger.error(f"AI хариулт авахад алдаа: {e}")
        error_msg = "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."
        if retry_count == 0 and conv_id:
            send_teams_notification(conv_id, message_content, customer_email,
                                    "AI системийн алдаа (Exception)",
                                    f"Python exception: {str(e)}, Thread ID: {thread_id}")
        return error_msg

def send_teams_notification(conv_id, customer_message, customer_email=None, escalation_reason="Хэрэглэгчийн асуудал", ai_analysis=None):
    """Microsoft Teams-д техникийн мэдээлэл илгээх"""
    if not TEAMS_WEBHOOK_URL:
        return False
    try:
        conv_url = f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
        error_summary = escalation_reason
        if ai_analysis:
            error_summary += f"\n\nAI анализ: {ai_analysis}"
        teams_message = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.3",
                    "body": [
                        {"type": "TextBlock", "text": "📋 Хэрэглэгчийн дүгнэлт", "weight": "Bolder", "size": "Medium", "color": "Attention"},
                        {"type": "TextBlock", "text": "AI системтэй дүн шинжилгээ хийгдэж байна.", "wrap": True},
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
            teams_message["attachments"][0]["content"]["body"].append({
                "type": "TextBlock", "text": "🤖 AI Дүгнэлт:", "weight": "Bolder", "size": "Medium", "spacing": "Large"
            })
            teams_message["attachments"][0]["content"]["body"].append({
                "type": "TextBlock", "text": ai_analysis, "wrap": True, "fontType": "Monospace", "color": "Good"
            })
        teams_message["attachments"][0]["content"]["actions"] = [
            {"type": "Action.OpenUrl", "title": "Chatwoot дээр харах", "url": conv_url}
        ]
        response = requests.post(TEAMS_WEBHOOK_URL, json=teams_message)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Teams мэдээлэл илгээхэд алдаа: {e}")
        return False

# ===================== Flask Routes =====================

@app.route("/verify", methods=["GET"])
def verify_email():
    """Имэйл баталгаажуулах endpoint"""
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

        # Contact-ийн баталгаажуулалт тохиргоо хадгалах
        update_contact(contact_id, {
            "email_verified": "1",
            "verified_email": email,
            "verification_date": datetime.utcnow().isoformat()
        })

        # Conversation дээр thread ключийг шинэчлэх
        thread_key = f"openai_thread_{contact_id}"
        update_conversation(conv_id, {thread_key: None})

        # Chatwoot-д баталгаажсан мессеж илгээх
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
    """Chatwoot webhook handler"""
    try:
        data = request.json
        logger.info(f"🔄 Webhook ирлээ: {data.get('message_type', 'unknown')}")

        # Зөвхөн incoming мессеж боловсруулна
        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped - not incoming"}), 200

        conv_id = data["conversation"]["id"]
        message_content = data.get("content", "").strip()

        logger.info(f"📝 Conversation ID: {conv_id}, Хэрэглэгчийн мессеж: '{message_content}'")

        # Contact ID олох
        contact_id = None
        if "sender" in data and data["sender"]:
            contact_id = data["sender"].get("id")

        if not contact_id:
            logger.warning("❌ Contact ID олдсонгүй")
            send_to_chatwoot(conv_id, "Алдаа: Хэрэглэгчийн мэдээлэл олдсонгүй.")
            return jsonify({"status": "error - no contact"}), 400

        logger.info(f"👤 Contact ID: {contact_id}")

        # ========== Баталгаажуулалт шалгах ==========
        is_verified = False
        verified_email = ""

        # Webhook дээрээс баталгаажсан эсэх үзэх
        if "conversation" in data and "meta" in data["conversation"] and "sender" in data["conversation"]["meta"]:
            sender_meta = data["conversation"]["meta"]["sender"]
            if "custom_attributes" in sender_meta:
                attrs = sender_meta["custom_attributes"]
                email_verified_value = attrs.get("email_verified", "")
                verified_email = attrs.get("verified_email", "")
                is_verified = str(email_verified_value).lower() in ["true", "1", "yes"]
                logger.info(f"Webhook-ээс баталгаажуулсан: {is_verified}, verified_email={verified_email}")

        # Хэрвээ webhook-д баталгаажуулалт байхгүй бол API-аар дахин шалгах
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

        # ========== Баталгаажуулалт хийх үйлдэл ==========
        if not is_verified:
            logger.info("🚫 Баталгаажуулалтгүй - имэйл шаардаж байна")
            # Имэйл хэлбэрийн мессеж эсэхийг шалгах
            if is_valid_email(message_content):
                token = generate_verification_token(message_content, conv_id, contact_id)
                if send_verification_email(message_content, token):
                    send_to_chatwoot(conv_id,
                        f"📧 Таны имэйл ({message_content}) рүү баталгаажуулах линк илгээлээ.\n"
                        "Имэйлээ шалгаад линк дээр дарна уу. Линк 24h-д хугацаа дуусна.\n"
                        "⚠️ Spam фолдерыг шалгахаа мартуузай!")
                    logger.info("✅ Имэйл амжилттай илгээлээ")
                else:
                    send_to_chatwoot(conv_id, "❌ Имэйл илгээхэд алдаа гарлаа.")
                    logger.error("❌ Имэйл илгээхэд алдаа")
            else:
                send_to_chatwoot(conv_id,
                    "👋 Сайн байна уу! Chatbot ашиглахын тулд эхлээд имэйл хаягаа баталгаажуулна уу.\n"
                    "📧 Жишээ: example@gmail.com")
            return jsonify({"status": "waiting_verification"}), 200

        # ========== AI ASSISTANT-тай харилцах ==========
        logger.info("🤖 AI Assistant ажиллаж байна...")

        # Conversation-аас thread_id авах эсвэл шинээр үүсгэх
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

        # AI Assistant руу хүсэлт илгээж хариулт авах
        ai_response_text = None
        ai_success = False

        def run_ai_assistant():
            nonlocal ai_response_text, ai_success
            try:
                retry_count = 0
                while retry_count <= MAX_AI_RETRIES:
                    response = get_ai_response(thread_id, message_content, conv_id, verified_email, retry_count)
                    if not any(err in response for err in ["алдаа гарлаа", "хэт удаж", "олдсонгүй"]):
                        ai_response_text = response
                        ai_success = True
                        logger.info(f"✅ AI хариулт: {response[:50]}...")
                        break
                    retry_count += 1
                    if retry_count <= MAX_AI_RETRIES:
                        logger.info(f"🔄 AI дахин оролдож байна... ({retry_count}/{MAX_AI_RETRIES})")
                        time.sleep(2)
                if not ai_success:
                    logger.error("❌ AI бүх оролдлогоо алдлаа")
            except Exception as e:
                logger.error(f"❌ AI алдаа: {e}")

        ai_thread = threading.Thread(target=run_ai_assistant)
        ai_thread.start()
        ai_thread.join(timeout=30)

        logger.info(f"🔍 AI амжилттай: {ai_success}")

        # Хариулт бэлдэх
        if ai_success:
            final_response = ai_response_text
            response_type = "AI Assistant"
        else:
            # Хоёр систем (RAG) аваагүй тул алдаатай fallback
            final_response = (
                "🚨 Уучлаарай, техникийн алдаа гарлаа. "
                "Таны асуултыг техникийн багт дамжуулав. Удахгүй хариулт өгөх болно."
            )
            response_type = "Error - Escalated"
            # Шаардлагатай бол Teams рүү мэдээлэх:
            try:
                send_teams_notification(conv_id, message_content, verified_email,
                                        "AI Assistant хариулт алдаатай", None)
            except Exception as e:
                logger.error(f"❌ Teams рүү мэдээлэх алдаа: {e}")

        # Chatwoot руу хариулт илгээх
        send_to_chatwoot(conv_id, final_response)
        logger.info(f"✅ {response_type} хариулт илгээлээ: {final_response[:50]}...")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

@app.route("/health", methods=["GET"])
def health():
    """Системийн health check"""
    status = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "openai_client": client is not None,
            "teams_webhook": TEAMS_WEBHOOK_URL is not None if TEAMS_WEBHOOK_URL else False,
            "email_smtp": SENDER_EMAIL is not None and SENDER_PASSWORD is not None,
            "chatwoot_api": CHATWOOT_API_KEY is not None and ACCOUNT_ID is not None
        }
    }
    all_ok = all(status["components"].values())
    if not all_ok:
        status["status"] = "warning"
    return jsonify(status), 200 if all_ok else 206

if __name__ == "__main__":
    app.run(debug=True, port=5000)
