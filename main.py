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

    # 1) Хэрэглэгчийн бичсэн мессежийн текст:
    message = data.get("content") or ""

    # 2) Conversation ID-г шууд авч байна:
    #    SaaS Chatwoot-д webhook payload-д 'conversation_id' гэж тодорхой талбартай ирдэг.
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        # Хэрвээ conversation_id байхгүй бол лог хийж, ямар payload ирж байгааг хараарай:
        print("Webhook payload-д conversation_id олдсонгүй:", data)
        return jsonify({"error": "conversation_id missing"}), 400

    # AI-r хариу үүсгэх
    ai_reply = call_ai_model(message)

    # Chatwoot API ашиглан хэрэглэгч рүү хариу илгээх
    send_reply(conversation_id, ai_reply)

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(port=5001)
