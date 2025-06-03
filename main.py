from flask import Flask, request, jsonify
import os, requests

app = Flask(__name__)

CHATWOOT_API_KEY = os.getenv("CHATWOOT_API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
BASE_URL = "https://app.chatwoot.com"  # Hosted Chatwoot URL

def send_reply(conversation_id: int, reply_text: str):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "api_access_token": CHATWOOT_API_KEY,
    }
    payload = {
        "content": reply_text,
        "message_type": "outgoing"
    }
    r = requests.post(url, json=payload, headers=headers)
    r.raise_for_status()

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    if data.get("message_type") != "incoming":
        return jsonify({"status": "ignored"}), 200

    conversation_id = data.get("conversation", {}).get("id")
    content = data.get("content", "")

    reply = f"Сайн байна уу! Таны бичсэн: {content}"
    send_reply(conversation_id, reply)

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
