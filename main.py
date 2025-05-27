import os
import time
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# Орчны хувьсагчид
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID      = os.environ["ASSISTANT_ID"]
CHATWOOT_API_KEY  = os.environ["CHATWOOT_API_KEY"]
ACCOUNT_ID        = os.environ["ACCOUNT_ID"]
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

# OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY)

# Chatwoot helper-ууд
def get_conversation(conv_id):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    return requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY}).json()

def update_conversation(conv_id, attrs):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    payload = {"conversation": {"custom_attributes": attrs}}
    return requests.put(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})

def send_to_chatwoot(conv_id, text):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    payload = {"content": text, "message_type": "outgoing"}
    r = requests.post(url, json=payload, headers=headers)
    print("Chatwoot status:", r.status_code)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if data.get("message_type") != "incoming":
        return jsonify({"status":"skipped"})

    conv_id = data["conversation"]["id"]
    conv = get_conversation(conv_id)
    thread_id = conv["conversation"]["custom_attributes"].get("thread_id")

    # Шинэ Thread үүсгэх эсвэл өмнөхөө үргэлжлүүлэх
    if not thread_id:
        thread = client.beta.threads.create()
        thread_id = thread.id
        update_conversation(conv_id, {"thread_id": thread_id})
        print(f"🆕 Created thread {thread_id} for conversation {conv_id}")
    else:
        print(f"↪️ Continuing thread {thread_id}")

    # Хэрэглэгчийн мессеж нэмэх
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=data["content"]
    )

    # Run эхлүүлэх
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID
    )

    # Run дуусахыг хүлээх
    while True:
        status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id).status
        if status == "completed":
            break
        time.sleep(1)

    # Хариултыг авах
    messages = client.beta.threads.messages.list(thread_id=thread_id).data
    for msg in messages:
        if msg.role == "assistant":
            # content нь list of blocks
            for block in msg.content:
                if block.type == "text":
                    reply = block.text.value
                    send_to_chatwoot(conv_id, reply)
                    break
            break

    return jsonify({"status":"ok"})

if __name__ == "__main__":
    app.run(port=5000)
