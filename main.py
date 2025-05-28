import os
import time
import requests
import re
import jwt
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
from openai import OpenAI

app = Flask(__name__)

# Орчны хувьсагчид
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID      = os.environ["ASSISTANT_ID"]
CHATWOOT_API_KEY  = os.environ["CHATWOOT_API_KEY"]
ACCOUNT_ID        = os.environ["ACCOUNT_ID"]
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

# Email тохиргоо
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD = os.environ["SENDER_PASSWORD"]

# JWT тохиргоо
JWT_SECRET = os.environ.get("JWT_SECRET", "your-secret-key-here")
VERIFICATION_URL_BASE = os.environ.get("VERIFICATION_URL_BASE", "http://localhost:5000")

# OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY)

def is_valid_email(email):
    """Имэйл хаягийн форматыг шалгах"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def generate_verification_token(email, conv_id, contact_id):
    """Баталгаажуулах JWT токен үүсгэх"""
    payload = {
        'email': email,
        'conv_id': conv_id,
        'contact_id': contact_id,
        'exp': datetime.utcnow() + timedelta(hours=24)  # 24 цагийн дараа дуусна
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    """JWT токеныг шалгах"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def send_verification_email(email, token):
    """Баталгаажуулах имэйл илгээх"""
    try:
        verification_url = f"{VERIFICATION_URL_BASE}/verify?token={token}"
        
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = email
        msg['Subject'] = "Имэйл хаягаа баталгаажуулна уу"
        
        body = f"""
        Сайн байна уу!
        
        Таны имэйл хаягийг баталгаажуулахын тулд доорх линк дээр дарна уу:
        
        {verification_url}
        
        Энэ линк 24 цагийн дараа хүчингүй болно.
        
        Хэрэв та биш бол бидэнд мэдэгдэнэ үү.
        
        Баярлалаа!
        """
        
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        return True
    except Exception as e:
        print(f"Имэйл илгээхэд алдаа: {e}")
        return False

def get_contact(contact_id):
    """Contact мэдээлэл авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def update_contact(contact_id, attrs):
    """Contact-ийн custom attributes шинэчлэх"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    payload = {"custom_attributes": attrs}
    resp = requests.put(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def get_conversation(conv_id):
    """Conversation мэдээлэл авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def update_conversation(conv_id, attrs):
    """Conversation-ийн custom attributes шинэчлэх"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes"
    payload = {"custom_attributes": attrs}
    resp = requests.post(url, json=payload, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def send_to_chatwoot(conv_id, text):
    """Chatwoot руу мессеж илгээх"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    payload = {"content": text, "message_type": "outgoing"}
    r = requests.post(url, json=payload, headers=headers)
    r.raise_for_status()

def get_ai_response(thread_id, message_content):
    """OpenAI Assistant-ээс хариулт авах"""
    try:
        # Хэрэглэгчийн мессежийг thread руу нэмэх
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=message_content
        )

        # Assistant run үүсгэх
        run = client.beta.threads.runs.create(
            thread_id=thread_id, 
            assistant_id=ASSISTANT_ID
        )

        # Run дуусахыг хүлээх
        max_wait = 30
        wait_count = 0
        while wait_count < max_wait:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread_id, 
                run_id=run.id
            )
            
            if run_status.status == "completed":
                break
            elif run_status.status in ["failed", "cancelled", "expired"]:
                return "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."
                
            time.sleep(1)
            wait_count += 1

        if wait_count >= max_wait:
            return "Хариулахад хэт удаж байна. Дахин оролдоно уу."

        # Assistant-ийн хариультыг авах
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        
        for msg in messages.data:
            if msg.role == "assistant":
                reply = ""
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        reply += content_block.text.value
                return reply

        return "Хариулт олдсонгүй. Дахин оролдоно уу."
        
    except Exception as e:
        print(f"AI хариулт авахад алдаа: {e}")
        return "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."

@app.route("/verify", methods=["GET"])
def verify_email():
    """Имэйл баталгаажуулах endpoint"""
    token = request.args.get('token')
    if not token:
        return "Токен олдсонгүй!", 400
    
    payload = verify_token(token)
    if not payload:
        return "Токен хүчингүй эсвэл хугацаа дууссан!", 400
    
    try:
        # Contact level дээр email_verified = true гэж тэмдэглэх
        conv_id = payload['conv_id']
        contact_id = payload['contact_id']
        email = payload['email']
        
        # Contact дээр баталгаажуулалтын мэдээлэл хадгалах
        update_contact(contact_id, {
            "email_verified": "1",  # Checkbox type-д string "true" ашиглах
            "verified_email": email,
            "verification_date": datetime.utcnow().isoformat()
        })
        
        # Conversation дээр thread мэдээлэл хадгалах (thread нь conversation specific)
        thread_key = f"openai_thread_{contact_id}"
        update_conversation(conv_id, {
            thread_key: None  # Шинэ thread эхлүүлэх
        })
        
        # Баталгаажуулах мессеж илгээх
        send_to_chatwoot(conv_id, f"✅ Таны имэйл хаяг ({email}) амжилттай баталгаажлаа! Одоо та chatbot-той харилцаж болно.")
        
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Имэйл баталгаажлаа</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                .success { color: green; font-size: 24px; margin: 20px 0; }
                .info { color: #666; font-size: 16px; }
            </style>
        </head>
        <body>
            <div class="success">✅ Амжилттай баталгаажлаа!</div>
            <div class="info">Таны имэйл хаяг ({{ email }}) баталгаажлаа.<br>Одоо та chatbot-тойгоо харилцаж болно.</div>
        </body>
        </html>
        """, email=email)
        
    except Exception as e:
        print(f"Verification алдаа: {e}")
        return "Баталгаажуулахад алдаа гарлаа!", 500

@app.route("/webhook", methods=["POST"])
def webhook():
    """Chatwoot webhook handler - бүрэн шинэ логик"""
    try:
        data = request.json
        print(f"🔄 Webhook received: {data.get('message_type', 'unknown')}")
        
        # Зөвхөн incoming мессеж боловсруулах
        if data.get("message_type") != "incoming":
            print("⏭️ Skipping: not incoming message")
            return jsonify({"status": "skipped - not incoming"}), 200

        # Үндсэн мэдээлэл авах
        conv_id = data["conversation"]["id"]
        message_content = data.get("content", "").strip()
        
        print(f"📝 Conv ID: {conv_id}, Message: '{message_content}'")
        
        # Contact ID олох
        contact_id = None
        if "sender" in data and data["sender"]:
            contact_id = data["sender"].get("id")
        
        if not contact_id:
            print("❌ Contact ID олдсонгүй")
            send_to_chatwoot(conv_id, "Алдаа: Хэрэглэгчийн мэдээлэл олдсонгүй.")
            return jsonify({"status": "error - no contact"}), 400

        print(f"👤 Contact ID: {contact_id}")

        # ========== БАТАЛГААЖУУЛАЛТ ШАЛГАХ ==========
        print("🔍 Баталгаажуулалт шалгаж байна...")
        
        # Contact-ийн custom attributes авах (webhook-ээс шууд)
        is_verified = False
        verified_email = ""
        
        # Webhook дотор contact мэдээлэл байгаа эсэхийг шалгах
        if "conversation" in data and "meta" in data["conversation"] and "sender" in data["conversation"]["meta"]:
            sender_meta = data["conversation"]["meta"]["sender"]
            if "custom_attributes" in sender_meta:
                contact_attrs = sender_meta["custom_attributes"]
                email_verified_value = contact_attrs.get("email_verified", "")
                verified_email = contact_attrs.get("verified_email", "")
                
                # Баталгаажуулалт шалгах
                is_verified = str(email_verified_value).lower() in ["true", "1", "yes"]
                
                print(f"📊 Webhook-ээс авсан: email_verified='{email_verified_value}', verified_email='{verified_email}'")
                print(f"✅ Is verified: {is_verified}")
        
        # Хэрэв webhook дээр байхгүй бол API-аар дахин шалгах
        if not is_verified:
            print("🔍 API-аар дахин шалгаж байна...")
            try:
                contact = get_contact(contact_id)
                contact_attrs = contact.get("custom_attributes", {})
                email_verified_value = contact_attrs.get("email_verified", "")
                verified_email = contact_attrs.get("verified_email", "")
                
                is_verified = str(email_verified_value).lower() in ["true", "1", "yes"]
                print(f"📊 API-аас авсан: email_verified='{email_verified_value}', verified_email='{verified_email}'")
                print(f"✅ Is verified: {is_verified}")
            except Exception as e:
                print(f"❌ API алдаа: {e}")
                is_verified = False

        # ========== БАТАЛГААЖУУЛАЛТЫН ҮЙЛДЭЛ ==========
        if not is_verified:
            print("🚫 Баталгаажуулаагүй - имэйл шаардаж байна")
            
            # Имэйл хаяг шалгах
            if is_valid_email(message_content):
                print(f"📧 Зөв имэйл: {message_content}")
                
                # Баталгаажуулах токен үүсгэх
                token = generate_verification_token(message_content, conv_id, contact_id)
                
                # Имэйл илгээх
                if send_verification_email(message_content, token):
                    send_to_chatwoot(conv_id, 
                        f"📧 Таны имэйл хаяг ({message_content}) рүү баталгаажуулах линк илгээлээ.\n\n"
                        "Имэйлээ шалгаад линк дээр дарна уу. Линк 24 цагийн дараа хүчингүй болно.\n\n"
                        "⚠️ Spam фолдерыг шалгахаа мартуузай!")
                    print("✅ Имэйл амжилттай илгээлээ")
                else:
                    send_to_chatwoot(conv_id, "❌ Имэйл илгээхэд алдаа гарлаа. Дахин оролдоно уу.")
                    print("❌ Имэйл илгээхэд алдаа")
            else:
                print(f"❌ Буруу имэйл формат: '{message_content}'")
                send_to_chatwoot(conv_id, 
                    "👋 Сайн байна уу! Chatbot ашиглахын тулд эхлээд имэйл хаягаа баталгаажуулна уу.\n\n"
                    "📧 Зөв имэйл хаягаа бичээд илгээнэ үү.\n"
                    "Жишээ: example@gmail.com")
            
            return jsonify({"status": "waiting_verification"}), 200

        # ========== AI CHATBOT АЖИЛЛУУЛАХ ==========
        print(f"🤖 Баталгаажсан хэрэглэгч ({verified_email}) - AI chatbot ажиллуулж байна")
        
        # Thread мэдээлэл авах
        conv = get_conversation(conv_id)
        conv_attrs = conv.get("custom_attributes", {})
        
        thread_key = f"openai_thread_{contact_id}"
        thread_id = conv_attrs.get(thread_key)
        
        # Thread шинээр үүсгэх хэрэгтэй эсэхийг шалгах
        if not thread_id:
            print("🧵 Шинэ thread үүсгэж байна...")
            thread = client.beta.threads.create()
            thread_id = thread.id
            update_conversation(conv_id, {thread_key: thread_id})
            print(f"✅ Thread үүсгэлээ: {thread_id}")
        else:
            print(f"🧵 Одоо байгаа thread ашиглаж байна: {thread_id}")

        # AI хариулт авах
        print("🤖 AI хариулт авч байна...")
        ai_response = get_ai_response(thread_id, message_content)
        
        # Chatwoot руу илгээх
        send_to_chatwoot(conv_id, ai_response)
        print(f"✅ AI хариулт илгээлээ: {ai_response[:50]}...")
        
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

@app.route("/bot/teams", methods=["POST"])
def teams_bot_handler():
    data = request.json
    print(f"📩 Teams message received: {data}")

    # Teams-с ирсэн мессежээс текст авах
    user_text = data.get("text", "").strip()
    if not user_text:
        return jsonify({"type": "message", "text": "⚠️ Мессеж хоосон байна."}), 200

    # ⚠️ Та энд conv_id-г өөрийн системтэй уях логик оруулж болно
    conv_id = os.environ.get("DEFAULT_CONV_ID")  # эсвэл API-р харгалзах conv_id олж болно

    if not conv_id:
        return jsonify({"type": "message", "text": "❌ Conv ID тохируулаагүй байна."}), 200

    # Chatwoot руу agent хариу илгээх
    try:
        send_to_chatwoot(conv_id, f"💬 Teams Agent: {user_text}")
        return jsonify({"type": "message", "text": "✅ Chatwoot руу илгээлээ!"}), 200
    except Exception as e:
        print(f"❌ Chatwoot-д илгээхэд алдаа: {e}")
        return jsonify({"type": "message", "text": "❌ Chatwoot руу илгээж чадсангүй."}), 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)