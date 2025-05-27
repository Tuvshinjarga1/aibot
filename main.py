import os
import time
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

# OpenAI client
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

    # 1) Thread үүсгэх
    thread = client.beta.threads.create()
    thread_id = thread.id

    # 2) Мессеж нэмэх
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=data["content"]
    )

    # 3) Run эхлүүлэх
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
    )

    # 4) Run-г гүйцэт дуустал хүлээх (poll)
    while True:
        run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run_status.status == "completed":
            break
        time.sleep(1)

    # 5) Хариултыг авах
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    for msg in messages.data:
        if msg.role == "assistant":
            for part in msg.content:
                if part.type == "text":
                    reply = part.text.value
                    send_to_chatwoot(data["conversation"]["id"], reply)
                    break
            break

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(port=5000)
