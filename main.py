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
    print("🔥 Raw webhook payload:", payload)

    # Pick apart top-level vs data.message format
    if "data" in payload and isinstance(payload["data"], dict):
        event       = payload.get("event")
        data        = payload["data"]
        message_src = data.get("message", {})
        conv_src    = data.get("conversation", {})
    else:
        event       = payload.get("event")
        message_src = payload
        conv_src    = payload.get("conversation", {})

    # Only care about actual new messages
    if event != "message_created":
        print("↩ Ignored event:", event)
        return jsonify({"status": "ignored_event"}), 200

    content  = (message_src.get("content") or "").strip()
    msg_type = message_src.get("message_type")
    conv_id  = conv_src.get("id")
    print(f"→ Parsed: type={msg_type!r}, content={content!r}, conv_id={conv_id!r}")

    # Only respond to user messages
    if msg_type not in ("incoming", 0, "0") or not conv_id:
        print("↩ Ignoring non-user message or missing conv_id")
        return jsonify({"status": "ignored"}), 200

    # Build your reply
    if content.lower() == "hi":
        reply_text = "Hello, таньд юугаар туслах вэ?"
    else:
        reply_text = content  # echo

    # 1) Reopen the conversation (if it was resolved/closed)
    reopen_url = (
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/"
        f"{CHATWOOT_ACCOUNT_ID}/conversations/"
        f"{conv_id}/reopen"
    )
    headers = {
        "api_access_token": CHATWOOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    # Fire-and-forget; if it fails, we still try to send the message
    try:
        requests.post(reopen_url, headers=headers, timeout=3)
    except Exception as e:
        print("⚠ Failed to reopen conversation:", e)

    # 2) Send your reply
    post_url = (
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/"
        f"{CHATWOOT_ACCOUNT_ID}/conversations/"
        f"{conv_id}/messages"
    )
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

    # 3) Mark as unread so agents see the red-dot notification
    unread_url = (
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/"
        f"{CHATWOOT_ACCOUNT_ID}/conversations/"
        f"{conv_id}/mark_as_unread"
    )
    try:
        requests.post(unread_url, headers=headers, timeout=3)
    except Exception as e:
        print("⚠ Failed to mark as unread:", e)

    return jsonify({"status": "ok", "replied": reply_text}), 200

if __name__ == "__main__":
    # debug=True for auto-reload; prints go to stdout
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
