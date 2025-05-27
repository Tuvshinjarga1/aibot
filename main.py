import os
import openai
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# Chatwoot API
CHATWOOT_API_KEY = os.environ["CHATWOOT_API_KEY"]
CHATWOOT_BASE_URL = "https://app.chatwoot.com"
ACCOUNT_ID = os.environ["ACCOUNT_ID"]

# OpenAI / OpenRouter API
ASSISTANT_API_KEY    = os.environ.get("ASSISTANT_API_KEY")    # ChatGPT Assistants v2 key
OPENROUTER_API_KEY   = os.environ.get("OPENROUTER_API_KEY")   # OpenRouter key

# Хэрвээ OpenRouter ашиглах бол base_url тохируулна
if OPENROUTER_API_KEY:
    openai.api_key   = OPENROUTER_API_KEY
    openai.api_base  = "https://api.openrouter.ai/v1"
else:
    openai.api_key   = ASSISTANT_API_KEY  # эсвэл шууд OpenAI-ийн албан ёсны түлхүүр
    # default base_url нь openai.com тул өөрчлөх шаардлагагүй

def ask_ai(message):
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # эсвэл өөр шаардлагатай модель
            messages=[
                {"role": "system", "content":
                    "Та зөвхөн https://cloud.mn сайттай холбоотой асуултад монгол хэлээр товч бөгөөд үнэн зөв хариулна уу."
                },
                {"role": "user", "content": message}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("❌ OpenAI/OpenRouter error:", e)
        return "AI серверт холбогдож чадсангүй."

def send_to_chatwoot(conversation_id, reply):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "api_access_token": CHATWOOT_API_KEY
    }
    payload = {"content": reply, "message_type": "outgoing"}
    resp = requests.post(url, json=payload, headers=headers)
    print("Chatwoot status:", resp.status_code, resp.text)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Webhook received:", data)
    if data.get("message_type") != "incoming":
        print("⛔ Skip non-incoming")
        return jsonify({"status": "skipped"})
    message = data["content"]
    conv_id = data["conversation"]["id"]
    ai_reply = ask_ai(message)
    print("🧠 AI reply:", ai_reply)
    send_to_chatwoot(conv_id, ai_reply)
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(port=5000)
