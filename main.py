from flask import Flask, request, jsonify
import requests
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

# Chatwoot тохиргоо
CHATWOOT_API_KEY = os.getenv("CHATWOOT_API_KEY")
CHATWOOT_BASE_URL = "https://app.chatwoot.com"
ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")

# OpenAI клиент
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__)

def call_ai_model(message: str) -> str:
    chat_completion = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": message}]
    )
    return chat_completion.choices[0].message.content.strip()

def send_reply(conversation_id, content):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "api_access_token": CHATWOOT_API_KEY
    }
    payload = {
        "content": content,
        "message_type": "outgoing"
    }
    requests.post(url, headers=headers, json=payload)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    event = data.get("event")

    # Зөвхөн шинэ мессеж үүссэн үед (message_created) ажиллана
    if event != "message_created":
        return jsonify({"status": "ignored"}), 200

    # Шинэ мессежийн текст
    message = data.get("content", "")
    if not message:
        return jsonify({"error": "No content in message_created"}), 400

    # Conversation ID-г conversation обьект доторх id-аас авна
    conversation = data.get("conversation", {})
    conversation_id = conversation.get("id")
    if not conversation_id:
        return jsonify({"error": "conversation.id missing"}), 400

    # AI-гаас хариу авна
    ai_reply = call_ai_model(message)

    # Chatwoot API-р дамжуулан reply явуулна
    send_reply(conversation_id, ai_reply)
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(port=5001)
