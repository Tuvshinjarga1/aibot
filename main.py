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
    resp.raise_for_status()
    # JSON нь top-level дээр conversation объект шивнүүргүйгээр буцаана
    return resp.json()

def update_conversation(conv_id, attrs):
    # "Add custom attributes" endpoint (POST) ашиглах
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes"
    payload = {"custom_attributes": attrs}
    resp = requests.post(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def send_to_chatwoot(conv_id, text):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    payload = {"content": text, "message_type": "outgoing"}
    r = requests.post(url, json=payload, headers=headers)
    r.raise_for_status()

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if data.get("message_type") != "incoming":
        return jsonify({"status":"skipped"}), 200

    conv_id = data["conversation"]["id"]
    conv    = get_conversation(conv_id)
    attrs   = conv.get("custom_attributes", {})
    thread_id = attrs.get("thread_id")

    # thread_id байхгүй бол шинээр үүсгээд custom_attributes-д хадгална
    if not thread_id:
        thread = client.beta.threads.create()
        thread_id = thread.id
        update_conversation(conv_id, {"thread_id": thread_id})

    # Хэрэглэгчийн мессежийг thread руу нэмнэ
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=data["content"]
    )

    # Run үүсгээд статус шалгана
    run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)
    while client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id).status != "completed":
        time.sleep(1)

    # Сүүлийн assistant хариултыг аваад Chatwoot руу илгээх
    msgs = client.beta.threads.messages.list(thread_id=thread_id).data
    for msg in reversed(msgs):
        if msg.role == "assistant":
            reply = "".join(
                block.text.value
                for block in msg.content
                if block.type == "text"
            )
            break

    send_to_chatwoot(conv_id, reply)
    return jsonify({"status":"ok"}), 200