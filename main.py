from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Эдгээрийг орчны хувьсагчаас ачаалбал аюулгүй
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "123470")
CHATWOOT_API_TOKEN = os.getenv("CHATWOOT_API_TOKEN", "Go61PtbAmeXrmmQineSiQyv3")
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

@app.route("/webhook/chatwoot", methods=["POST"])
def chatwoot_webhook():
    payload = request.get_json(force=True)
    
    # Ирсэн мессежийн мэдээлэл
    msg = payload.get("message", {})
    content = msg.get("content", "").strip()
    msg_type = msg.get("message_type")  # 0 = incoming (user), 1 = outgoing (agent)
    conv = payload.get("conversation", {})
    conversation_id = conv.get("id")
    
    # Баталгаажуулалт
    if msg_type != 0 or not conversation_id:
        # Энд бид зөвхөн хэрэглэгчээс (incoming) мессежд хариулна
        return jsonify({"status": "ignored"}), 200
    
    # Хариу бичих text-ийг тодорхойлно
    if content.lower() == "hi":
        reply_text = "Hello, би танд юугаар туслах вэ?"
    else:
        # Өөр бүх тохиолдолд default хариулт
        reply_text = "Баярлалаа! Та асуух зүйлээ тодорхой бичнэ үү."

    # Chatwoot API руу POST
    url = (
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/"
        f"{CHATWOOT_ACCOUNT_ID}/conversations/"
        f"{conversation_id}/messages"
    )
    headers = {
        "api_access_token": CHATWOOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {"content": reply_text}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        # Алдаа тохиолдвол 502 буцаана
        return jsonify({"error": str(e), "response": getattr(e.response, "text", "")}), 502

    return jsonify({"status": "ok", "reply": reply_text}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
