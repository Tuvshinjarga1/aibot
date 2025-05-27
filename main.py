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
    try:
        data = request.json
        print(f"Received webhook data: {data}")  # Debug үүсгэх
        
        # Зөвхөн incoming мессеж боловсруулах
        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped - not incoming"}), 200

        # Conversation болон contact мэдээлэл авах
        conv_id = data["conversation"]["id"]
        
        # Contact ID-г олон арга замаар оролдох
        contact_id = None
        if "sender" in data and data["sender"]:
            contact_id = data["sender"].get("id")
        elif "contact" in data and data["contact"]:
            contact_id = data["contact"].get("id")
        
        if not contact_id:
            print("Contact ID олдсонгүй!")
            return jsonify({"status": "error - no contact ID"}), 400

        print(f"Conversation ID: {conv_id}, Contact ID: {contact_id}")

        # Conversation-ийн мэдээлэл авах
        conv = get_conversation(conv_id)
        attrs = conv.get("custom_attributes", {})
        
        # Хэрэглэгч тус бүрийн thread key үүсгэх
        thread_key = f"openai_thread_{contact_id}"
        thread_id = attrs.get(thread_key)
        
        print(f"Thread key: {thread_key}, Existing thread ID: {thread_id}")

        # Thread байхгүй бол шинээр үүсгэх
        if not thread_id:
            print("Шинэ thread үүсгэж байна...")
            thread = client.beta.threads.create()
            thread_id = thread.id
            
            # Custom attributes руу хадгалах
            try:
                update_conversation(conv_id, {thread_key: thread_id})
                print(f"Thread ID хадгалагдлаа: {thread_id}")
            except Exception as e:
                print(f"Thread ID хадгалахад алдаа: {e}")
                return jsonify({"status": "error saving thread"}), 500
        else:
            print(f"Одоо байгаа thread ашиглаж байна: {thread_id}")

        # Хэрэглэгчийн мессежийг thread руу нэмэх
        message_content = data.get("content", "").strip()
        if not message_content:
            return jsonify({"status": "skipped - empty content"}), 200

        print(f"Мессеж нэмэж байна: {message_content}")
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=message_content
        )

        # Assistant run үүсгэх
        print("Assistant run үүсгэж байна...")
        run = client.beta.threads.runs.create(
            thread_id=thread_id, 
            assistant_id=ASSISTANT_ID
        )

        # Run дуусахыг хүлээх
        max_wait = 30  # 30 секунд хүлээх
        wait_count = 0
        while wait_count < max_wait:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread_id, 
                run_id=run.id
            )
            print(f"Run status: {run_status.status}")
            
            if run_status.status == "completed":
                break
            elif run_status.status in ["failed", "cancelled", "expired"]:
                print(f"Run failed with status: {run_status.status}")
                return jsonify({"status": f"run failed: {run_status.status}"}), 500
                
            time.sleep(1)
            wait_count += 1

        if wait_count >= max_wait:
            print("Run timeout!")
            return jsonify({"status": "timeout"}), 500

        # Assistant-ийн хариултыг авах
        print("Assistant хариулт авч байна...")
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        
        # Хамгийн сүүлийн assistant мессежийг олох
        reply = None
        for msg in messages.data:
            if msg.role == "assistant":
                reply = ""
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        reply += content_block.text.value
                break

        if not reply:
            print("Assistant хариулт олдсонгүй!")
            return jsonify({"status": "no assistant reply"}), 500

        # Chatwoot руу хариулт илгээх
        print(f"Chatwoot руу илгээж байна: {reply}")
        send_to_chatwoot(conv_id, reply)
        
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500