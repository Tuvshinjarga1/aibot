import os
import time
import requests
import re
import threading
from flask import Flask, request, jsonify

# ── Environment load ───────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── Logging ─────────────────────────────────────────────────────────────────────
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Orchny huvisagchid ─────────────────────────────────────────────────────────
CHATWOOT_API_KEY  = os.getenv("CHATWOOT_API_KEY", "").strip()
ACCOUNT_ID        = os.getenv("ACCOUNT_ID", "").strip()
INBOX_ID          = os.getenv("INBOX_ID", "").strip()
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com").rstrip("/")

# (Имэйл болон баталгаажуулахгүй тул доорх утгуудыг хоосон байлгаж болно)
SENDER_EMAIL      = os.getenv("SENDER_EMAIL", "").strip()
SENDER_PASSWORD   = os.getenv("SENDER_PASSWORD", "").strip()
SMTP_SERVER       = os.getenv("SMTP_SERVER", "").strip()
SMTP_PORT         = int(os.getenv("SMTP_PORT", "587"))
VERIFICATION_URL_BASE = os.getenv("VERIFICATION_URL_BASE", "").strip()
JWT_SECRET        = os.getenv("JWT_SECRET", "").strip()
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "").strip()

# ── Chatwoot функцууд ──────────────────────────────────────────────────────────

def send_to_chatwoot(conv_id: int, text: str) -> None:
    """
    Chatwoot руу outgoing (агентын) мессеж илгээх
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

def create_conversation(contact_id: int) -> int:
    """
    Шинэ Conversation үүсгэх (API Channel Inbox руу)
    """
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "contact_id": contact_id,
        "inbox_id": INBOX_ID     # API Channel Inbox ID
    }
    resp = requests.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    conv = resp.json()["payload"]["conversation"]
    return conv["id"]

def create_or_update_contact(email_or_name: str) -> int:
    """
    Хэрвээ email байсан бол тухайн Contact-ийг хайж, байвал ID-ийг буцаах.
    Байхгүй бол шинээр үүсгээд ID буцаах.
    Заавал email биш бол бид name талбар дээр шууд өгч болно.
    """
    # 1) Хайлтаар шалгах (имэйлээр)
    search_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/search"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    resp = requests.get(search_url, params={"q": email_or_name}, headers=headers)
    resp.raise_for_status()
    payload = resp.json().get("payload", [])
    if payload:
        # Хэрвээ contact олдвол тэр ID-ийг буцаах
        existing = payload[0]
        return existing["id"]

    # 2) Шинээр үүсгэх (имэйлгүй бол name талбарт шууд бичнэ)
    create_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    # Хэрвээ зөв имэйл форматын эсэхийг шалгаад болгоомжтой ажиллаж болно.
    is_email = re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email_or_name)
    contact_data = {
        "name": email_or_name if not is_email else email_or_name.split("@")[0],
        "email": email_or_name if is_email else None
    }
    resp = requests.post(create_url, json=contact_data, headers=headers)
    resp.raise_for_status()
    new_contact = resp.json()["payload"]["contact"]
    return new_contact["id"]

# ── Flask Routes ────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook_handler():
    """
    Chatwoot webhook handler. 
    Обработчик incoming мессеж ирэхэд ажиллана.
    """
    try:
        data = request.json or {}
        logger.info(f"🔄 Webhook ирлээ: {data.get('message_type', 'unknown')}")

        # Зөвхөн incoming мессеж боловсруулах
        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped - not incoming"}), 200

        # 1) Conversation ID болон мессежийн content
        conv_id = data["conversation"].get("id")    # Зарим тохиолдолд conv_id байхгүй байж болно
        message_content = (data.get("content") or "").strip()
        logger.info(f"📝 conv_id={conv_id}, content='{message_content}'")

        # 2) Contact ID олох
        contact_id = None
        if data.get("sender") and data["sender"].get("id"):
            contact_id = data["sender"]["id"]

        # Хэрвээ contact_id байхгүй бол (API Channel-аар ирсэн анхны мессеж) → Шинэ Contact үүсгэх
        if not contact_id:
            # Манай жишээ: хэрвээ message_content нь имэйл бол үүнийг ашиглан үүсгэнэ, эс бөгөөс name гэж үүсгэнэ.
            contact_id = create_or_update_contact(message_content or "AnonymousUser")
            # Мөн шууд шинэ Conversation үүсгэх
            conv_id = create_conversation(contact_id)
            logger.info(f"👤 Шинэ Contact ID={contact_id}, Шинэ Conversation ID={conv_id}")

        else:
            # Хэрвээ conv_id ирээгүй тохиолдолд (зарим webhook-д сирол байж болно)
            if not conv_id:
                conv_id = create_conversation(contact_id)
                logger.info(f"👤 Бөглөөгүй Conversation ID байсан тул шинэ Conversation ID={conv_id}")

        # 3) Хариулт бэлтгэх (энэ жишээнд бид “echo” маягаар хариулт буцаана)
        reply_text = f"Бот хариулт: \"{message_content}\""
        logger.info(f"🤖 Reply текст бэлтгэлээ: {reply_text}")

        # 4) Chatwoot руу outgoing мессеж явуулах
        send_to_chatwoot(conv_id, reply_text)

        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

if __name__ == "__main__":
    # debug=True бол алдаа гарах үед дэлгэрэнгүй log харуулна
    app.run(host="0.0.0.0", port=5000, debug=True)
