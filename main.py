import os
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Орчны хувьсагчид
CHATWOOT_API_KEY = os.getenv("CHATWOOT_API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
INBOX_ID = os.getenv("INBOX_ID")
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com").rstrip("/")

# 🧩 Contact-ийг Inbox-той холбох
def ensure_contact_inbox(contact_id: int, inbox_id: int, source_id: str = "default-source"):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contact_inboxes"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "contact_id": contact_id,
        "inbox_id": inbox_id,
        "source_id": source_id
    }
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()

# 🧩 Contact үүсгэх
def create_or_update_contact(name: str, email: str = None) -> int:
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "name": name,
        "email": email
    }
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()["payload"]["contact"]["id"]

# 🧩 Conversation үүсгэх
def create_conversation(contact_id: int) -> int:
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "contact_id": contact_id,
        "inbox_id": INBOX_ID
    }
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()["payload"]["conversation"]["id"]

# 🧩 Outgoing message илгээх
def send_to_chatwoot(conv_id: int, text: str):
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
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()

# 🧪 Webhook handler
@app.route("/webhook", methods=["POST"])
def webhook_handler():
    data = request.json or {}

    if data.get("message_type") != "incoming":
        return jsonify({"status": "skipped"}), 200

    message = (data.get("content") or "").strip()
    if not message:
        return jsonify({"error": "No message"}), 400

    contact_name = data.get("sender", {}).get("name", "User")
    contact_email = data.get("sender", {}).get("email")  # Optional

    # 1. Contact үүсгэх
    contact_id = create_or_update_contact(contact_name, contact_email)

    # 2. Contact-ийг Inbox-той холбох
    ensure_contact_inbox(contact_id, int(INBOX_ID))

    # 3. Conversation үүсгэх
    conv_id = create_conversation(contact_id)

    # 4. Хариу илгээх
    reply = f"🤖 Бот хариулт: \"{message}\""
    send_to_chatwoot(conv_id, reply)

    return jsonify({"status": "success"}), 200

# Health check
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
