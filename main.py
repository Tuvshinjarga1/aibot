from flask import Flask, request, jsonify
import requests
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

# Chatwoot тохиргоо
CHATWOOT_API_KEY = os.getenv("CHATWOOT_API_KEY")
CHATWOOT_BASE_URL = "https://app.chatwoot.com"
ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")

# OpenAI клиент
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("ERROR: OPENAI_API_KEY байхгүй байна!")
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)

def call_ai_model(message: str) -> str:
    try:
        print(f"[AI] Илгээх текст: {message}")
        chat_completion = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": message}]
        )
        reply = chat_completion.choices[0].message.content.strip()
        print(f"[AI] Ирсэн хариу: {reply}")
        return reply
    except Exception as e:
        # Алдааны дэлгэрэнгүй мэдээллийг харахын тулд print-д e болон өөрийн stacktrace-г хэвлэж болно
        print("ERROR: AI дуудлага хийх үед алдаа гарлаа:", e)
        return "Уучлаарай, дотоод алдаа гарлаа."

def send_reply(conversation_id, content):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "api_access_token": CHATWOOT_API_KEY
    }
    payload = {
        "content": content,
        "message_type": "outgoing"
    }
    try:
        print(f"[Chatwoot] Reply явуулж байна → conversation_id={conversation_id}, content={content}")
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        print(f"[Chatwoot] Амжилттай илгээгдлээ (status_code={response.status_code})")
        return True
    except Exception as e:
        print("ERROR: Chatwoot руу хариу илгээх үед алдаа гарлаа:", e)
        return False

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True) or {}
    event = data.get("event")
    print(f"[Webhook] Ирсэн event: {event}")
    # Илүү дэлгэрэнгүй payload харах бол доорх мөрийг идэвхжүүлнэ:
    # print(f"[Webhook] Payload: {data}")

    # Шинэ мессеж (message_created) бус event-үүдийг үл тоомсорлоно
    if event != "message_created":
        print(f"[Webhook] Үл тоомсорлолоо (event != message_created): {event}")
        return jsonify({"status": "ignored"}), 200

    # Шинэ мессеж агуулга
    message = data.get("content", "")
    if not message:
        print("WARNING: message_created event-д content хоосон байна")
        return jsonify({"error": "No content in message_created"}), 400
    print(f"[Webhook] Шинэ мессеж: {message}")

    # Conversation ID авах
    conversation = data.get("conversation", {})
    conversation_id = conversation.get("id")
    if not conversation_id:
        print("ERROR: conversation.id байхгүй байна! Payload:", data)
        return jsonify({"error": "conversation.id missing"}), 400
    print(f"[Webhook] Conversation ID: {conversation_id}")

    # AI-д илгээж хариу авах
    ai_reply = call_ai_model(message)

    # Chatwoot руу reply явуулах
    success = send_reply(conversation_id, ai_reply)
    if not success:
        print(f"ERROR: conversation_id={conversation_id} руу хариу явуулагдсангүй")
        return jsonify({"status": "failed_to_send"}), 500

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    # debug=True бол ажлын явцыг Flask-аас илүү дэлгэрэнгүй харуулна
    print("Bot сервер эхэллээ (port=5001)...")
    app.run(port=5001)
