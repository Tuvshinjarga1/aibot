import os
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

CHATWOOT_API_KEY = os.getenv("CHATWOOT_API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
INBOX_ID = os.getenv("INBOX_ID")
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com")

# 1. Contact үүсгэх
def create_contact(name, email):
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

# 2. Conversation үүсгэх
def create_conversation(contact_id):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "source_id": contact_id,  # Contact ID
        "inbox_id": int(INBOX_ID)
    }
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()["payload"]["conversation"]["id"]

# 3. Message илгээх
def send_message(conv_id, message):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "content": message,
        "message_type": "outgoing"
    }
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()

@app.route("/start", methods=["POST"])
def start_conversation():
    try:
        data = request.json
        name = data.get("name", "Guest")
        email = data.get("email", "guest@example.com")
        user_message = data.get("message", "Сайн байна уу?")

        contact_id = create_contact(name, email)
        conv_id = create_conversation(contact_id)
        send_message(conv_id, f"Сайн байна уу, {name}! Таны мессеж: {user_message}")

        return jsonify({"status": "conversation_started", "conversation_id": conv_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
