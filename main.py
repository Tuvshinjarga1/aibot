from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# You can also load this from env vars for security
CHATWOOT_ACCOUNT_ID = "123470"
CHATWOOT_CONVERSATION_ID = "12"
CHATWOOT_API_TOKEN = "Go61PtbAmeXrmmQineSiQyv3"
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

@app.route("/webhook/chatwoot", methods=["POST"])
def send_to_chatwoot():
    # Expecting JSON like: { "content": "Your message here" }
    data = request.get_json(force=True)
    content = data.get("content")
    if not content:
        return jsonify({"error": "Missing 'content' in request body"}), 400

    url = (
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/"
        f"{CHATWOOT_ACCOUNT_ID}/conversations/"
        f"{CHATWOOT_CONVERSATION_ID}/messages"
    )
    payload = {"content": content}
    headers = {
        "api_access_token": CHATWOOT_API_TOKEN,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        return jsonify({"error": str(e), "response": getattr(e.response, "text", "")}), 502

    return jsonify(resp.json()), resp.status_code

if __name__ == "__main__":
    # Listen on port 5000 by default
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
