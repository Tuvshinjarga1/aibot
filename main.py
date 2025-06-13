from flask import Flask, request, jsonify
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

@app.route("/webhook/chatwoot", methods=["POST"])
def webhook():
    try:
        body = request.get_json()
        message = body.get("message", {}).get("content")

        if message == "Hi":
            url = "https://app.chatwoot.com/api/v1/accounts/123470/conversations/12/messages"
            data = {
                "content": "Hello, би танд юугаар туслах вэ?",
                "message_type": "outgoing"
            }
            headers = {
                "api_access_token": os.getenv("CHATWOOT_API_KEY"),
                "Content-Type": "application/json"
            }

            print("📤 Sending message to Chatwoot...")
            response = requests.post(url, json=data, headers=headers)
            print(f"📥 Chatwoot response: {response.status_code} - {response.text}")

        return jsonify({"status": "received"})

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
