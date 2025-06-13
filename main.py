import os
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# ——— CONFIG ———
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "123470")
CHATWOOT_API_TOKEN   = os.getenv("CHATWOOT_API_TOKEN", "Go61PtbAmeXrmmQineSiQyv3")
CHATWOOT_BASE_URL    = "https://app.chatwoot.com"

@app.route("/webhook/chatwoot", methods=["POST"])
def chatwoot_webhook():
    payload = request.get_json(force=True)
    print("🔥 Webhook received:", payload)

    # Only handle new messages
    if payload.get("event") != "message_created":
        return jsonify({"status": "ignored_event"}), 200

    data = payload.get("data", {})
    msg  = data.get("message", {})
    conv = data.get("conversation", {})

    content  = (msg.get("content") or "").strip()
    msg_type = msg.get("message_type")
    conv_id  = conv.get("id")

    print(f"→ message_created: type={msg_type} content={content!r} conv_id={conv_id}")

    # Accept both string and numeric "incoming"
    if (msg_type not in ("incoming", 0, "0")) or not conv_id:
        print("↩ Ignoring non-user message or missing conv_id")
        return jsonify({"status": "ignored"}), 200

    # Decide reply
    if content.lower() == "hi":
        reply_text = "Hello, таньд юугаар туслах вэ?"
    else:
        # Echo back any other text
        reply_text = content

    # Post back to Chatwoot
    post_url = (
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/"
        f"{CHATWOOT_ACCOUNT_ID}/conversations/"
        f"{conv_id}/messages"
    )
    headers = {
        "api_access_token": CHATWOOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    body = {
        "content":      reply_text,
        "message_type": "outgoing"
    }

    try:
        resp = requests.post(post_url, json=body, headers=headers, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        print("⚠ Failed to send reply:", e, getattr(e.response, "text", ""))
        return jsonify({
            "error":    str(e),
            "response": getattr(e.response, "text", "")
        }), 502

    print("✅ Replied with:", repr(reply_text))
    return jsonify({"status": "ok", "replied": reply_text}), 200

if __name__ == "__main__":
    # debug=True will auto-reload on changes
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
