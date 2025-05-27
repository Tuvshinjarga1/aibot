from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# 🛠 Chatwoot API тохиргоо
CHATWOOT_API_KEY = os.environ.get('CHATWOOT_API_KEY')
CHATWOOT_BASE_URL = 'https://app.chatwoot.com'  # эсвэл өөрийн chatwoot сервер
ACCOUNT_ID = os.environ.get('ACCOUNT_ID')

# 🧠 AI API тохиргоо (жишээ: Groq)
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

def ask_ai(message):
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": "llama3-70b-8192",
        "messages": [
            {
                "role": "system",
                "content": "Та зөвхөн https://cloud.mn сайттай холбоотой асуултад монгол хэлээр ойлгомжтой хариу өгнө үү. Хариултаа товч бөгөөд үнэн зөв өгнө үү."
            },
            {
                "role": "user",
                "content": message
            }
        ]
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)

    try:
        data = response.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        else:
            print("⚠️ Groq API response error:", data)
            return "AI бот хариу өгөх боломжгүй байна. Түр азнаад дахин оролдоно уу."
    except Exception as e:
        print("❌ JSON parse error:", e)
        return "AI серверт холбогдож чадсангүй."

def send_to_chatwoot(conversation_id, reply):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "api_access_token": CHATWOOT_API_KEY
    }
    payload = {
        "content": reply,
        "message_type": "outgoing"
    }
    response = requests.post(url, json=payload, headers=headers)

    # ✅ Логлох
    print(f"Chatwoot API status: {response.status_code}")
    print(f"Chatwoot response: {response.text}")


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Webhook received:", data)

    try:
        # 🛑 Зөвхөн хэрэглэгчийн ирсэн мессеж (message_type: 0) дээр ажиллана
        if data.get('message_type') != 0:
            print("⛔ Skip non-incoming message (e.g. outgoing bot reply)")
            return jsonify({"status": "skipped"})

        # 🟢 Эндээс эхлээд AI-д дамжуулах
        message = data['content']
        conversation_id = data['conversation']['id']

        ai_reply = ask_ai(message)
        send_to_chatwoot(conversation_id, ai_reply)
        return jsonify({"status": "ok"})

    except Exception as e:
        print("Error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(port=5000)
