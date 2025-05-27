import os
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# 🌐 Орчны хувьсагчид
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY")
ASSISTANT_ID      = os.environ.get("ASSISTANT_ID")
CHATWOOT_API_KEY  = os.environ.get("CHATWOOT_API_KEY")
ACCOUNT_ID        = os.environ.get("ACCOUNT_ID")
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

# ✅ Шалгалт
for name, val in [
    ("OPENAI_API_KEY", OPENAI_API_KEY),
    ("ASSISTANT_ID", ASSISTANT_ID),
    ("CHATWOOT_API_KEY", CHATWOOT_API_KEY),
    ("ACCOUNT_ID", ACCOUNT_ID),
]:
    if not val:
        raise RuntimeError(f"Орчны хувьсагч дутуу: {name}")

# 🎛️ OpenAI Assistants клиент
client = OpenAI(api_key=OPENAI_API_KEY)

def send_to_chatwoot(conv_id, text):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    payload = {"content": text, "message_type": "outgoing"}
    resp = requests.post(url, json=payload, headers=headers)
    print("Chatwoot status:", resp.status_code, resp.text)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if data.get("message_type") != "incoming":
        return jsonify({"status": "skipped"})

    # 1) Thread үүсгэх ба мессэж нэмэх
    thread = client.beta.assistants.threads.create(assistant_id=ASSISTANT_ID)
    thread_id = thread["id"]
    client.beta.assistants.threads.messages.create(
        assistant_id=ASSISTANT_ID,
        thread_id=thread_id,
        content=data["content"],
        role="user"
    )

    # 2) Run үүсгэх
    run = client.beta.assistants.runs.create(
        assistant_id=ASSISTANT_ID,
        thread_id=thread_id
    )
    run_id = run["id"]
    # 3) Статус шалгах (тестэд товчоор poll хийх)
    while True:
        status = client.beta.assistants.runs.get(
            assistant_id=ASSISTANT_ID,
            run_id=run_id
        )["status"]
        if status == "complete":
            break

    # 4) Хариултыг авч Chatwoot руу илгээх
    messages = client.beta.assistants.threads.messages.list(
        assistant_id=ASSISTANT_ID,
        thread_id=thread_id
    )
    bot_reply = next(msg for msg in messages if msg["role"] == "assistant")["content"]
    send_to_chatwoot(data["conversation"]["id"], bot_reply)

    return jsonify({"status":"ok"})

if __name__ == "__main__":
    app.run(port=5000)
