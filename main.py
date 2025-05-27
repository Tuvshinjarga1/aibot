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

def get_conversation(conv_id):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    data = resp.json()
    # payload доторх conversation-ыг буцаана
    return data["payload"]["conversation"]

def update_conversation(conv_id, attrs):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    payload = {"conversation": {"custom_attributes": attrs}}
    return requests.put(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if data.get("message_type") != "incoming":
        return jsonify({"status":"skipped"})

    conv_id = data["conversation"]["id"]
    conv = get_conversation(conv_id)
    attrs = conv.get("custom_attributes", {})
    thread_id = attrs.get("thread_id")

    if not thread_id:
        # Шинэ thread үүсгэх
        thread = client.beta.threads.create()
        thread_id = thread.id
        # custom_attributes-д хадгална
        update_conversation(conv_id, {"thread_id": thread_id})

    # Хэрэглэгчийн мессежийг thread руу нэмнэ
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=data["content"]
    )

    # Run үүсгээд хариулт хүлээх
    run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)
    while True:
        status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id).status
        if status == "completed":
            break
        time.sleep(1)

    # Бүх мессежүүдийн дундаас хамгийн сүүлийн assistant хариултыг авна
    msgs = client.beta.threads.messages.list(thread_id=thread_id).data
    # reverse хийж сүүлийн assistant message-ийг авна
    for msg in reversed(msgs):
        if msg.role == "assistant":
            # content нь list of blocks учир боломжит бүх text block-ээс нийлбэр гаргана
            reply = "".join(
                block.text.value
                for block in msg.content
                if block.type == "text"
            )
            break

    send_to_chatwoot(conv_id, reply)
    return jsonify({"status":"ok"})