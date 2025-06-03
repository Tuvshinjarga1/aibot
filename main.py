import os
import requests
from flask import Flask, request, jsonify

# ── Орчны хувьсагчид ─────────────────────────────────────────────
CHATWOOT_API_KEY = "XzyB8zXpdFNvtbAL3xdtid3r"  # <-- Таны admin token
ACCOUNT_ID = "122224"
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

# ── Flask App ───────────────────────────────────────────────────
app = Flask(__name__)

# ── Chatwoot руу мессеж илгээх ─────────────────────────────────
def send_to_chatwoot(conversation_id, message_text):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "content": message_text,
        "message_type": "outgoing",  # Агентын хариу
        "private": False
    }
    response = requests.post(url, json=payload, headers=headers)
    print("📨 Response:", response.status_code)
    print(response.text)
    response.raise_for_status()

# ── Webhook хүлээн авах ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    
    # Зөвхөн incoming мессеж
    if data.get("message_type") != "incoming":
        return jsonify({"status": "ignored"}), 200

    conv_id = data.get("conversation", {}).get("id")
    content = data.get("content", "").strip()

    if not conv_id or not content:
        return jsonify({"status": "invalid"}), 400

    # Ботын хариу
    reply = f"Бот хариулт: \"{content}\""
    send_to_chatwoot(conv_id, reply)

    return jsonify({"status": "replied"}), 200

# ── Health check ────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

# ── App run ─────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
