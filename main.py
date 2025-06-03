from flask import Flask, request, jsonify
import requests
import openai
from dotenv import load_dotenv
import os

# .env ачааллах
load_dotenv()

# Орчны хувьсагчуудаас утгууд авах
CHATWOOT_API_TOKEN = os.getenv("CHATWOOT_API_TOKEN")
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL")
ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

app = Flask(__name__)

def call_ai_model(message: str) -> str:
    res = openai.ChatCompletion.create(
        model="gpt-4",  # эсвэл "gpt-3.5-turbo"
        messages=[{"role": "user", "content": message}]
    )
    return res.choices[0].message.content.strip()

def send_reply(conversation_id, content):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "api_access_token": CHATWOOT_API_TOKEN
    }
    payload = {
        "content": content,
        "message_type": "outgoing"
    }
    requests.post(url, headers=headers, json=payload)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    message = data.get("content")
    conversation_id = data["conversation"]["id"]

    ai_reply = call_ai_model(message)
    send_reply(conversation_id, ai_reply)

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(port=5001)
