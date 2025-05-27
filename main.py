from flask import Flask, request, jsonify
import os
from openai import OpenAI
import requests

app = Flask(__name__)
client = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID = os.environ["ASSISTANT_ID"]
CHATWOOT_KEY = os.environ["CHATWOOT_API_KEY"]
ACCOUNT_ID   = os.environ["ACCOUNT_ID"]
BASE_URL     = "https://app.chatwoot.com"

def send_to_chatwoot(conv_id, text):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {"api_access_token": CHATWOOT_KEY}
    payload = {"content": text, "message_type": "outgoing"}
    requests.post(url, json=payload, headers=headers)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if data.get("message_type") != "incoming":
        return jsonify({"status":"skipped"})
    # 1. Thread үүсгэх ба мессеж нэмэх
    thread = client.beta.assistants.threads.create(assistant_id=ASSISTANT_ID)
    thread_id = thread["id"]
    client.beta.assistants.threads.messages.create(
        assistant_id=ASSISTANT_ID,
        thread_id=thread_id,
        content=data["content"],
        role="user"
    )
    # 2. Run үүсгэх
    run = client.beta.assistants.runs.create(
        assistant_id=ASSISTANT_ID,
        thread_id=thread_id
    )
    # 3. Статусыг шалгах (дараа давтан шалгах эсвэл webhook ашиглах)
    run_id = run["id"]
    # (интерактив polling эсвэл event-driven реализацийг сонгоно)
    # товчоор complete болсны дараа хариултыг авч Chatwoot руу явуулна...
    # send_to_chatwoot(...)
    return jsonify({"status":"ok"})
