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
    # Conversation-оос custom_attributes татаж авна
    conv = requests.get(
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}",
        headers={"api_access_token": CHATWOOT_API_KEY}
    ).json()
    # direct custom_attributes-ийг аваад
    attrs = conv.get("custom_attributes", {})
    thread_id = attrs.get("thread_id")

    # Хэрвээ thread_id байхгүй бол шинээр үүсгээд хадгална
    if not thread_id:
        thread = client.beta.threads.create()
        thread_id = thread.id
        # Хадгалах
        requests.put(
            f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}",
            json={"custom_attributes": {"thread_id": thread_id}},
            headers={"api_access_token": CHATWOOT_API_KEY}
        )

    # Хэрэглэгчийн мессежийг thread руу нэмнэ
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=data["content"]
    )

    # Run үүсгэж хариулт авна (polling)
    run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)
    while client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id).status != "completed":
        time.sleep(1)

    # Хариултыг авч Chatwoot руу илгээх
    msgs = client.beta.threads.messages.list(thread_id=thread_id).data
    reply = next(
        block.text.value
        for msg in msgs if msg.role=="assistant"
        for block in msg.content if block.type=="text"
    )
    send_to_chatwoot(conv_id, reply)

    return jsonify({"status":"ok"})

if __name__ == "__main__":
    app.run(port=5000)
