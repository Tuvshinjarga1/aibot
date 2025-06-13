from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# It’s still best practice to load these from env vars:
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "123470")
CHATWOOT_API_TOKEN   = os.getenv("CHATWOOT_API_TOKEN", "Go61PtbAmeXrmmQineSiQyv3")
CHATWOOT_BASE_URL    = "https://app.chatwoot.com"

@app.route("/webhook/chatwoot", methods=["POST"])
def chatwoot_webhook():
    payload = request.get_json(force=True)
    data    = payload.get("data", {})

    # Grab the incoming message and its conversation
    msg          = data.get("message", {})
    conv         = data.get("conversation", {})
    content      = (msg.get("content") or "").strip()
    message_type = msg.get("message_type")    # e.g. "incoming" or "outgoing"
    conv_id      = conv.get("id")

    # Only respond to user‐sent messages
    if message_type != "incoming" or not conv_id:
        return jsonify({"status": "ignored"}), 200

    # If they say “hi”, send a special greeting; otherwise echo back
    if content.lower() == "hi":
        reply_text = "Hello, таньд юугаар туслах вэ?"
    else:
        reply_text = content

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
        return jsonify({
            "error":    str(e),
            "response": getattr(e.response, "text", "")
        }), 502

    return jsonify({"status": "ok", "replied": reply_text}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
