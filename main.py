from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# It’s best to load these from env vars in production
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "123470")
CHATWOOT_API_TOKEN = os.getenv("CHATWOOT_API_TOKEN", "Go61PtbAmeXrmmQineSiQyv3")
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

@app.route("/webhook/chatwoot", methods=["POST"])
def chatwoot_webhook():
    payload = request.get_json(force=True)
    
    # Only handle new messages
    if payload.get("event") != "message_created":
        return jsonify({"status": "ignored_event"}), 200

    data = payload.get("data", {})
    msg  = data.get("message", {})
    conv = data.get("conversation", {})

    content         = (msg.get("content") or "").strip()
    message_type    = msg.get("message_type")      # "incoming" or "outgoing"
    conversation_id = conv.get("id")

    # Only reply to incoming (user) messages
    if message_type != "incoming" or not conversation_id:
        return jsonify({"status": "ignored"}), 200

    # Your custom logic: if user says "hi", reply specially
    if content.lower() == "hi":
        reply_text = "Hello, би танд юугаар туслах вэ?"
    else:
        reply_text = "Баярлалаа! Та асуух зүйлээ тодорхой бичнэ үү."

    post_url = (
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/"
        f"{CHATWOOT_ACCOUNT_ID}/conversations/"
        f"{conversation_id}/messages"
    )
    headers = {
        "api_access_token": CHATWOOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    body = {
        "content": reply_text,
        "message_type": "outgoing"   # ensure Chatwoot treats this as your reply
    }

    try:
        resp = requests.post(post_url, json=body, headers=headers, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        return jsonify({
            "error": str(e),
            "response": getattr(e.response, "text", "")
        }), 502

    return jsonify({"status": "ok", "replied_with": reply_text}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
