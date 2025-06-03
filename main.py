import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── Орчны хувьсагчид ──────────────────────────────────────────────────────────
CHATWOOT_API_KEY  = os.getenv("CHATWOOT_API_KEY", "").strip()
ACCOUNT_ID        = os.getenv("ACCOUNT_ID", "").strip()
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com").rstrip("/")

def send_to_chatwoot(conv_id: int, text: str) -> None:
    """
    Chatwoot руу outgoing мессеж илгээх
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
    requests.post(url, json=payload, headers=headers).raise_for_status()

@app.route("/webhook", methods=["POST"])
def webhook_handler():
    """
    Chatwoot-аас incoming мессеж хүлээн авч
    ботын reply-ийг буцаана.
    """
    data = request.json or {}

    # Зөвхөн incoming мессеж боловсруулна
    if data.get("message_type") != "incoming":
        return jsonify({"status": "skipped"}), 200

    # Conversation ID ба мессежийн текст
    conv_id = data.get("conversation", {}).get("id")
    message_content = (data.get("content") or "").strip()

    if not conv_id or not message_content:
        return jsonify({"status": "error"}), 400

    # Энд та хүссэнээрээ reply текстээ бэлдэнэ
    # Жишээ нь: энгийн “echo”:
    reply_text = f"Бот хариулт: \"{message_content}\""

    # Chatwoot руу outgoing мессеж явуулах
    send_to_chatwoot(conv_id, reply_text)

    return jsonify({"status": "success"}), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
