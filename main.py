from flask import Flask, request, jsonify
import requests

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
        "model": "mixtral-8x7b-32768",  # эсвэл deepseek-chat
        "messages": [
            {"role": "user", "content": message}
        ]
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.json()['choices'][0]['message']['content']

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
    requests.post(url, json=payload, headers=headers)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Webhook received:", data)

    try:
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
