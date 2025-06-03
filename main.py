import os
import time
import requests
import re
import jwt
import smtplib
import hashlib
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

# Microsoft Teams тохиргоо
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")
MAX_AI_RETRIES = 2  # AI хэдэн удаа оролдсоны дараа ажилтанд хуваарилах

# JWT тохиргоо
JWT_SECRET = os.environ.get("JWT_SECRET", "your-secret-key-here")
VERIFICATION_URL_BASE = os.environ.get("VERIFICATION_URL_BASE", "http://localhost:5000")

# OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY)

# Имэйл rate limiting буферы (санах ойд хадгалах)
email_attempts = {}
MAX_EMAIL_ATTEMPTS = 3  # 1 цагт дээд тал нь 3 удаа
ATTEMPT_WINDOW = 3600   # 1 цаг (секундээр)

def is_valid_email(email):
    """Имэйл хаягийн форматыг шалгах - илүү нарийвчилсан"""
    if not email or len(email) > 254:  # RFC 5321 стандартын дагуу
        return False
    
    # Үндсэн regex шалгалт
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False
    
    # @ тэмдэг нэг байх ёстой
    if email.count('@') != 1:
        return False
    
    # Local ба domain хэсгийг тусгаарлах
    local, domain = email.split('@')
    
    # Local хэсгийн урт шалгах (64 тэмдэгтээс илүүгүй)
    if len(local) > 64:
        return False
    
    # Domain хэсгийн урт шалгах
    if len(domain) > 253:
        return False
    
    return True

def check_email_rate_limit(email):
    """Имэйл илгээх давтамжийг шалгах"""
    now = time.time()
    email_hash = hashlib.md5(email.encode()).hexdigest()
    
    if email_hash in email_attempts:
        # Хуучин оролдлогуудыг цэвэрлэх
        email_attempts[email_hash] = [
            timestamp for timestamp in email_attempts[email_hash] 
            if now - timestamp < ATTEMPT_WINDOW
        ]
        
        # Хэт олон оролдлого байгаа эсэхийг шалгах
        if len(email_attempts[email_hash]) >= MAX_EMAIL_ATTEMPTS:
            return False, f"Хэт олон оролдлого! {ATTEMPT_WINDOW//60} минутын дараа дахин оролдоно уу."
    else:
        email_attempts[email_hash] = []
    
    # Шинэ оролдлого нэмэх
    email_attempts[email_hash].append(now)
    return True, "OK"

def generate_verification_token(email, conv_id, contact_id):
    """Баталгаажуулах JWT токен үүсгэх - илүү хамгаалалттай"""
    payload = {
        'email': email.lower().strip(),  # Email-г normalize хийх
        'conv_id': str(conv_id),
        'contact_id': str(contact_id),
        'issued_at': datetime.utcnow().timestamp(),
        'exp': datetime.utcnow() + timedelta(hours=24)  # 24 цагийн дараа дуусна
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    """JWT токеныг шалгах - илүү дэлгэрэнгүй error handling"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        
        # Токений насыг дахин шалгах
        if 'exp' in payload:
            exp_time = payload['exp']
            if isinstance(exp_time, datetime):
                if datetime.utcnow() > exp_time:
                    return None
        
        return payload
    except jwt.ExpiredSignatureError:
        print("❌ JWT токен хугацаа дууссан")
        return None
    except jwt.InvalidTokenError as e:
        print(f"❌ JWT токен буруу: {e}")
        return None
    except Exception as e:
        print(f"❌ JWT токен шалгахад алдаа: {e}")
        return None

def test_email_connection():
    """SMTP холболтыг тест хийх"""
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.quit()
        return True, "SMTP холболт амжилттай"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP нэвтрэх нэр нууц үг буруу"
    except smtplib.SMTPConnectError:
        return False, f"SMTP сервертэй холбогдож чадсангүй: {SMTP_SERVER}:{SMTP_PORT}"
    except Exception as e:
        return False, f"SMTP холболтын алдаа: {str(e)}"

def send_verification_email(email, token):
    """Баталгаажуулах имэйл илгээх - HTML формат ашиглах"""
    try:
        # Rate limiting шалгах
        can_send, message = check_email_rate_limit(email)
        if not can_send:
            print(f"❌ Rate limit: {message}")
            return False, message
        
        # SMTP холболт тест хийх
        connection_ok, connection_msg = test_email_connection()
        if not connection_ok:
            print(f"❌ SMTP холболт: {connection_msg}")
            return False, connection_msg
        
        verification_url = f"{VERIFICATION_URL_BASE}/verify?token={token}"
        
        msg = MIMEMultipart('alternative')
        msg['From'] = SENDER_EMAIL
        msg['To'] = email
        msg['Subject'] = "🔐 Имэйл хаягаа баталгаажуулна уу"
        
        # HTML агуулга
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Имэйл баталгаажуулалт</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f5f5f5; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: white; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; }}
        .content {{ padding: 30px; }}
        .verify-button {{ 
            display: inline-block; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; 
            padding: 15px 30px; 
            text-decoration: none; 
            border-radius: 25px; 
            font-weight: bold;
            margin: 20px 0;
            text-align: center;
        }}
        .verify-button:hover {{ opacity: 0.9; }}
        .info-box {{ background-color: #e3f2fd; border-left: 4px solid #2196f3; padding: 15px; margin: 20px 0; }}
        .warning-box {{ background-color: #fff3e0; border-left: 4px solid #ff9800; padding: 15px; margin: 20px 0; }}
        .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #666; font-size: 12px; }}
        .logo {{ font-size: 24px; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">🤖 ChatBot</div>
            <h1>Имэйл баталгаажуулалт</h1>
        </div>
        
        <div class="content">
            <h2>👋 Сайн байна уу!</h2>
            <p>Таны имэйл хаягийг баталгаажуулахын тулд доорх товчлуур дээр дарна уу:</p>
            
            <div style="text-align: center;">
                <a href="{verification_url}" class="verify-button">
                    ✅ Имэйлээ баталгаажуулах
                </a>
            </div>
            
            <div class="info-box">
                <strong>💡 Мэдээлэл:</strong>
                <ul>
                    <li>Энэ линк зөвхөн 24 цагийн турш хүчинтэй</li>
                    <li>Баталгаажуулсны дараа та chatbot-той харилцаж болно</li>
                    <li>Аюулгүй байдлын үүднээс линкийг хуваалцахгүй байхыг зөвлөж байна</li>
                </ul>
            </div>
            
            <div class="warning-box">
                <strong>⚠️ Анхаар:</strong> Хэрэв та энэ имэйлийг хүссэнгүй бол бидэнд мэдэгдэнэ үү эсвэл энэ имэйлийг устгаарай.
            </div>
            
            <p>Хэрэв товчлуур ажиллахгүй байвал доорх линкийг хуулж, хөтчийн хаягийн талбарт буулгана уу:</p>
            <p style="word-break: break-all; background-color: #f8f9fa; padding: 10px; border-radius: 5px; font-family: monospace;">
                {verification_url}
            </p>
        </div>
        
        <div class="footer">
            <p>Энэ автомат илгээгдсэн имэйл юм. Хариу бичих шаардлагагүй.</p>
            <p>© 2024 ChatBot System. Бүх эрх хуулиар хамгаалагдсан.</p>
        </div>
    </div>
</body>
</html>
        """
        
        # Text агуулга (HTML дэмжихгүй имэйл клиентэд)
        text_body = f"""
🔐 Имэйл баталгаажуулалт

Сайн байна уу!

Таны имэйл хаягийг баталгаажуулахын тулд доорх линк дээр дарна уу:

{verification_url}

⏰ Энэ линк 24 цагийн дараа хүчингүй болно.

⚠️ Хэрэв та биш бол энэ имэйлийг устгана уу.

---
Энэ автомат илгээгдсэн имэйл юм.
© 2024 ChatBot System
        """
        
        # MIME хэсгүүд нэмэх
        text_part = MIMEText(text_body, 'plain', 'utf-8')
        html_part = MIMEText(html_body, 'html', 'utf-8')
        
        msg.attach(text_part)
        msg.attach(html_part)
        
        # Имэйл илгээх
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Имэйл амжилттай илгээлээ: {email}")
        return True, "Амжилттай илгээлээ"
        
    except smtplib.SMTPAuthenticationError as e:
        error_msg = "SMTP нэвтрэх алдаа - нэр нууц үг шалгана уу"
        print(f"❌ {error_msg}: {e}")
        return False, error_msg
    except smtplib.SMTPRecipientsRefused as e:
        error_msg = "Хүлээн авагчийн имэйл хаяг буруу"
        print(f"❌ {error_msg}: {e}")
        return False, error_msg
    except smtplib.SMTPServerDisconnected as e:
        error_msg = "SMTP сервертэй холболт тасарсан"
        print(f"❌ {error_msg}: {e}")
        return False, error_msg
    except Exception as e:
        error_msg = f"Имэйл илгээхэд алдаа: {str(e)}"
        print(f"❌ {error_msg}")
        return False, error_msg

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

def analyze_customer_issue(thread_id, current_message, customer_email=None):
    """AI ашиглан хэрэглэгчийн бүх чат түүхийг дүгнэж, comprehensive мэдээлэл өгөх"""
    try:
        # OpenAI thread-с бүх мессежийг авах
        messages = client.beta.threads.messages.list(thread_id=thread_id, limit=50)
        
        # Хэрэглэгчийн мессежүүдийг цуглуулах
        conversation_history = []
        for msg in reversed(messages.data):  # Эхнээс нь эрэмбэлэх
            if msg.role == "user":
                content = ""
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        content += content_block.text.value
                if content.strip():
                    conversation_history.append(f"Хэрэглэгч: {content.strip()}")
            elif msg.role == "assistant":
                content = ""
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        content += content_block.text.value
                if content.strip():
                    conversation_history.append(f"AI: {content.strip()[:200]}...")  # Хязгаарлах
        
        # Хэрэв чат түүх хоосон бол зөвхөн одоогийн мессежээр дүгнэх
        if not conversation_history:
            conversation_history = [f"Хэрэглэгч: {current_message}"]
        
        # Conversation түүхийг string болгох
        chat_history = "\n".join(conversation_history[-10:])  # Сүүлийн 10 мессеж
        
        # Илүү тодорхой system prompt
        system_msg = (
            "Та бол дэмжлэгийн мэргэжилтэн. "
            "Хэрэглэгчийн бүх чат түүхийг харж, асуудлыг иж бүрэн дүгнэж өгнө үү. "
            "Хэрэв олон асуудал байвал гол асуудлыг тодорхойлж фокуслана уу."
        )

        # Comprehensive user prompt
        user_msg = f'''
Хэрэглэгчийн чат түүх:
{chat_history}

Одоогийн мессеж: "{current_message}"

Дараах форматаар бүх чат түүхэд тулгуурлан дүгнэлт өгнө үү:

АСУУДЛЫН ТӨРӨЛ: [Техникийн/Худалдааны/Мэдээллийн/Гомдол]
ЯАРАЛТАЙ БАЙДАЛ: [Өндөр/Дунд/Бага] 
АСУУДЛЫН ТОВЧ ТАЙЛБАР: [Гол асуудлыг 1-2 өгүүлбэрээр]
ЧАТЫН ХЭВ МАЯГ: [Анхны асуулт/Дагалдах асуулт/Гомдол/Тодруулга хүсэх]
ШААРДЛАГАТАЙ АРГА ХЭМЖЭЭ: [Тодорхой арга хэмжээ]
ХҮЛЭЭГДЭЖ БУЙ ХАРИУЛТ: [Хэрэглэгч ямар хариулт хүлээж байгаа]
ДҮГНЭЛТ: [Ерөнхий үнэлгээ ба зөвлөмж]
'''

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=500,
            temperature=0.2
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"❌ Асуудал дүгнэхэд алдаа: {e}")
        return f"Асуудал дүгнэх боломжгүй. Хэрэглэгчийн одоогийн мессеж: {current_message}"

def send_teams_notification(conv_id, customer_message, customer_email=None, escalation_reason="Хэрэглэгчийн асуудал", ai_analysis=None):
    """Microsoft Teams руу техникийн асуудлын талаар ажилтанд мэдээлэх"""
    if not TEAMS_WEBHOOK_URL:
        print("⚠️ Teams webhook URL тохируулаагүй байна")
        return False
    
    try:
        # Chatwoot conversation URL
        conv_url = f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
        
        # AI асуудлын дэлгэрэнгүй мэдээлэл бэлтгэх
        error_summary = escalation_reason
        if ai_analysis:
            error_summary += f"\n\nДэлгэрэнгүй анализ: {ai_analysis}"
        
        # Teams message format
        teams_message = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.3",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "📋 Хэрэглэгчийн асуудлын дүгнэлт",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": "Attention"
                        },
                        {
                            "type": "TextBlock",
                            "text": "AI систем хэрэглэгчийн асуудлыг дүгнэж, ажилтны анхаарал татахуйц асуудал гэж үзэж байна.",
                            "wrap": True,
                            "color": "Default"
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {
                                    "title": "Харилцагч:",
                                    "value": customer_email or "Тодорхойгүй"
                                },
                                {
                                    "title": "Хэрэглэгчийн мессеж:",
                                    "value": customer_message[:300] + ("..." if len(customer_message) > 300 else "")
                                },
                                {
                                    "title": "Хугацаа:",
                                    "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                }
                            ]
                        }
                    ]
                }
            }]
        }
        
        # AI дүгнэлт нэмэх
        if ai_analysis:
            teams_message["attachments"][0]["content"]["body"].append({
                "type": "TextBlock",
                "text": "🤖 AI Дүгнэлт:",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Large"
            })
            teams_message["attachments"][0]["content"]["body"].append({
                "type": "TextBlock",
                "text": ai_analysis,
                "wrap": True,
                "fontType": "Monospace",
                "color": "Good"
            })
        
        # Actions нэмэх
        teams_message["attachments"][0]["content"]["actions"] = [
            {
                "type": "Action.OpenUrl",
                "title": "Chatwoot дээр харах",
                "url": conv_url
            }
        ]
        
        response = requests.post(TEAMS_WEBHOOK_URL, json=teams_message)
        response.raise_for_status()
        print(f"✅ Teams техникийн мэдээлэл илгээлээ: {escalation_reason}")
        return True
        
    except Exception as e:
        print(f"❌ Teams мэдээлэл илгээхэд алдаа: {e}")
        return False

def get_ai_response(thread_id, message_content, conv_id=None, customer_email=None, retry_count=0):
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
                error_msg = "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."
                
                # Teams мэдээлэх (хэрэв эхний удаагийн алдаа бол)
                if retry_count == 0 and conv_id:
                    send_teams_notification(
                        conv_id, 
                        message_content, 
                        customer_email, 
                        f"AI run статус алдаа: {run_status.status}",
                        f"OpenAI run ID: {run.id}, Status: {run_status.status}"
                    )
                
                return error_msg
                
            time.sleep(1)
            wait_count += 1

        if wait_count >= max_wait:
            timeout_msg = "Хариулахад хэт удаж байна. Дахин оролдоно уу."
            
            # Teams мэдээлэх (хэрэв эхний удаагийн timeout бол)
            if retry_count == 0 and conv_id:
                send_teams_notification(
                    conv_id, 
                    message_content, 
                    customer_email, 
                    "AI хариулт timeout (30 секунд)",
                    f"OpenAI run ID: {run.id}, Thread ID: {thread_id}"
                )
            
            return timeout_msg

        # Assistant-ийн хариультыг авах
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        
        for msg in messages.data:
            if msg.role == "assistant":
                reply = ""
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        reply += content_block.text.value
                return reply

        # Хариулт олдохгүй
        no_response_msg = "Хариулт олдсонгүй. Дахин оролдоно уу."
        
        # Teams мэдээлэх (хэрэв эхний удаагийн алдаа бол)
        if retry_count == 0 and conv_id:
            send_teams_notification(
                conv_id, 
                message_content, 
                customer_email, 
                "AI хариулт олдсонгүй",
                f"Thread ID: {thread_id}, Messages хайлтад хариулт байхгүй"
            )
        
        return no_response_msg
        
    except Exception as e:
        print(f"AI хариулт авахад алдаа: {e}")
        error_msg = "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."
        
        # Teams мэдээлэх (хэрэв эхний удаагийн алдаа бол)
        if retry_count == 0 and conv_id:
            send_teams_notification(
                conv_id, 
                message_content, 
                customer_email, 
                "AI системийн алдаа (Exception)",
                f"Python exception: {str(e)}, Thread ID: {thread_id}"
            )
        
        return error_msg

@app.route("/verify", methods=["GET"])
def verify_email():
    """Имэйл баталгаажуулах endpoint - илүү сайжруулсан"""
    token = request.args.get('token')
    if not token:
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Алдаа</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f5f5f5; }
                .error { color: #d32f2f; font-size: 24px; margin: 20px 0; }
                .info { color: #666; font-size: 16px; }
                .container { max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="error">❌ Токен олдсонгүй!</div>
                <div class="info">Баталгаажуулах линк буруу байна.</div>
            </div>
        </body>
        </html>
        """), 400
    
    payload = verify_token(token)
    if not payload:
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Токен хүчингүй</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f5f5f5; }
                .error { color: #d32f2f; font-size: 24px; margin: 20px 0; }
                .info { color: #666; font-size: 16px; }
                .container { max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="error">⏰ Токен хүчингүй!</div>
                <div class="info">Токений хугацаа дууссан эсвэл буруу байна.<br>Шинэ баталгаажуулах линк хүсээд имэйлээ дахин бичнэ үү.</div>
            </div>
        </body>
        </html>
        """), 400
    
    try:
        # Contact level дээр email_verified = true гэж тэмдэглэх
        conv_id = payload['conv_id']
        contact_id = payload['contact_id']
        email = payload['email']
        
        print(f"✅ Имэйл баталгаажуулалт: {email} (Contact: {contact_id}, Conv: {conv_id})")
        
        # Contact дээр баталгаажуулалтын мэдээлэл хадгалах
        verification_data = {
            "email_verified": "1",  # Checkbox type-д string "1" ашиглах
            "verified_email": email,
            "verification_date": datetime.utcnow().isoformat(),
            "verification_method": "email_link"
        }
        
        update_contact(contact_id, verification_data)
        print(f"✅ Contact {contact_id} шинэчлэлээ")
        
        # Conversation дээр thread мэдээлэл хадгалах (thread нь conversation specific)
        thread_key = f"openai_thread_{contact_id}"
        update_conversation(conv_id, {
            thread_key: None,  # Шинэ thread эхлүүлэх
            "last_verification": datetime.utcnow().isoformat()
        })
        print(f"✅ Conversation {conv_id} шинэчлэлээ")
        
        # Баталгаажуулах мессеж илгээх
        success_message = (
            f"🎉 Баярлалаа! Таны имэйл хаяг ({email}) амжилттай баталгаажлаа!\n\n"
            "✅ Одоо та chatbot-той бүрэн харилцаж болно.\n"
            "💬 Асуулт, хүсэлтээ бичээд илгээнэ үү."
        )
        send_to_chatwoot(conv_id, success_message)
        print(f"✅ Chatwoot-д амжилтын мессеж илгээлээ")
        
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Амжилттай баталгаажлаа</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body { 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    margin: 0; 
                    padding: 0; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                .container { 
                    max-width: 500px; 
                    background: white; 
                    padding: 40px; 
                    border-radius: 15px; 
                    box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                    text-align: center;
                }
                .success { 
                    color: #4caf50; 
                    font-size: 28px; 
                    margin: 20px 0; 
                    font-weight: bold;
                }
                .info { 
                    color: #333; 
                    font-size: 16px; 
                    line-height: 1.6;
                    margin: 20px 0;
                }
                .email { 
                    background-color: #e8f5e8; 
                    color: #2e7d32; 
                    padding: 10px; 
                    border-radius: 8px; 
                    font-weight: bold;
                    margin: 15px 0;
                }
                .next-steps {
                    background-color: #f0f7ff;
                    border-left: 4px solid #2196f3;
                    padding: 15px;
                    margin: 20px 0;
                    text-align: left;
                }
                .footer {
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid #eee;
                    color: #666;
                    font-size: 14px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="success">🎉 Амжилттай баталгаажлаа!</div>
                <div class="info">
                    Таны имэйл хаяг амжилттай баталгаажлаа:
                    <div class="email">{{ email }}</div>
                </div>
                
                <div class="next-steps">
                    <strong>📱 Дараагийн алхам:</strong>
                    <ul>
                        <li>Чат цонх руу буцаж очно уу</li>
                        <li>Асуулт, хүсэлтээ бичээд илгээнэ үү</li>
                        <li>Chatbot таныг танин мэдэж, тусалж эхэлнэ</li>
                    </ul>
                </div>
                
                <div class="footer">
                    <p>✅ Баталгаажуулалт: {{ verification_time }}</p>
                    <p>🤖 ChatBot System</p>
                </div>
            </div>
        </body>
        </html>
        """, email=email, verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
    except Exception as e:
        print(f"❌ Verification алдаа: {e}")
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Алдаа гарлаа</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f5f5f5; }
                .error { color: #d32f2f; font-size: 24px; margin: 20px 0; }
                .info { color: #666; font-size: 16px; }
                .container { max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="error">❌ Алдаа гарлаа!</div>
                <div class="info">Баталгаажуулахад техникийн алдаа гарлаа.<br>Дахин оролдоно уу.</div>
            </div>
        </body>
        </html>
        """), 500

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
            print(f"✅ Одоо байгаа thread ашиглаж байна: {thread_id}")

        # AI хариулт авах (retry logic-той)
        print("🤖 AI хариулт авч байна...")
        
        retry_count = 0
        ai_response = None
        
        while retry_count <= MAX_AI_RETRIES:
            ai_response = get_ai_response(thread_id, message_content, conv_id, verified_email, retry_count)
            
            # Хэрэв алдаатай хариулт биш бол амжилттай
            if not any(error_phrase in ai_response for error_phrase in [
                "алдаа гарлаа", "хэт удаж байна", "олдсонгүй"
            ]):
                break
                
            retry_count += 1
            if retry_count <= MAX_AI_RETRIES:
                print(f"🔄 AI дахин оролдож байна... ({retry_count}/{MAX_AI_RETRIES})")
                time.sleep(2)  # 2 секунд хүлээх
        
        # Хэрэв бүх оролдлого бүтэлгүйтвэл ажилтанд хуваарилах
        if retry_count > MAX_AI_RETRIES:
            print("❌ AI-ийн бүх оролдлого бүтэлгүйтэв - ажилтанд хуваарилж байна")
            
            send_teams_notification(
                conv_id, 
                message_content, 
                verified_email, 
                f"AI {MAX_AI_RETRIES + 1} удаа дараалан алдаа гаргалаа",
                f"Thread ID: {thread_id}, Бүх retry оролдлого бүтэлгүйтэв"
            )
            
            ai_response = (
                "🚨 Уучлаарай, техникийн асуудал гарлаа.\n\n"
                "Би таны асуултыг техникийн багт дамжуулаа. Удахгүй асуудлыг шийдэж, танд хариулт өгөх болно.\n\n"
                "🕐 Түр хүлээнэ үү..."
            )
        
        # Chatwoot руу илгээх
        send_to_chatwoot(conv_id, ai_response)
        print(f"✅ AI хариулт илгээлээ: {ai_response[:50]}...")
        
        # AI амжилттай хариулт өгсний дараа асуудлыг дүгнэж Teams-ээр мэдээлэх
        if retry_count <= MAX_AI_RETRIES:  # Зөвхөн амжилттай AI хариулт үед
            print("🔍 Teams-д илгээх хэрэгтэй эсэхийг шалгаж байна...")
            
            # Шинэ асуудал мөн эсэхийг шалгах
            should_escalate, reason = should_escalate_to_teams(thread_id, message_content)
            
            if should_escalate:
                print(f"✅ {reason} - Teams-д илгээх")
                try:
                    # AI-ээр асуудлыг дүгнэх
                    analysis = analyze_customer_issue(thread_id, message_content, verified_email)
                    print(f"✅ Дүгнэлт бэлэн: {analysis[:100]}...")
                    
                    # Teams-ээр мэдээлэх
                    send_teams_notification(
                        conv_id,
                        message_content,
                        verified_email,
                        f"Хэрэглэгчийн асуудлын дүгнэлт - {reason}",
                        analysis
                    )
                    print("✅ Асуудлын дүгнэлт ажилтанд илгээлээ")
                    
                except Exception as e:
                    print(f"❌ Асуудал дүгнэхэд алдаа: {e}")
            else:
                print(f"⏭️ {reason} - Teams-д илгээхгүй")
        
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

@app.route("/test-teams", methods=["GET"])
def test_teams():
    """Teams webhook тест хийх"""
    if not TEAMS_WEBHOOK_URL:
        return jsonify({"error": "TEAMS_WEBHOOK_URL тохируулаагүй байна"}), 400
    
    try:
        # Тест дүгнэлт үүсгэх
        test_analysis = """АСУУДЛЫН ТӨРӨЛ: Teams интеграцийн тест
ЯАРАЛТАЙ БАЙДАЛ: Бага
АСУУДЛЫН ТОВЧ ТАЙЛБАР: Систем зөвөөр ажиллаж байгаа эсэхийг шалгах зорилготой тест мэдээлэл.
ШААРДЛАГАТАЙ АРГА ХЭМЖЭЭ: Teams мэдээллийг ажилтан харж, системтэй танилцах
ХҮЛЭЭГДЭЖ БУЙ ХАРИУЛТ: "Тест амжилттай" гэсэн баталгаажуулалт"""
        
        # Тест мэдээлэл илгээх
        success = send_teams_notification(
            conv_id="test_123",
            customer_message="Энэ тест мэдээлэл юм. Teams холболт ажиллаж байгаа эсэхийг шалгаж байна.",
            customer_email="test@example.com",
            escalation_reason="Teams webhook тест",
            ai_analysis=test_analysis
        )
        
        if success:
            return jsonify({"status": "success", "message": "Teams мэдээлэл амжилттай илгээлээ!"}), 200
        else:
            return jsonify({"error": "Teams мэдээлэл илгээхэд алдаа"}), 500
            
    except Exception as e:
        return jsonify({"error": f"Алдаа: {str(e)}"}), 500

def escalate_to_human(conv_id, customer_message, customer_email=None):
    """Хэрэглэгчийн асуудлыг AI-ээр дүгнэж Teams-ээр ажилтанд хуваарилах (ашиглагддаггүй)"""
    try:
        print("🔍 Хэрэглэгчийн асуудлыг дүгнэж байна...")
        
        # Энэ функц ашиглагддаггүй учир простой дүгнэлт хийх
        simple_analysis = f"""АСУУДЛЫН ТӨРӨЛ: Тодорхойгүй
ЯАРАЛТАЙ БАЙДАЛ: Дунд
АСУУДЛЫН ТОВЧ ТАЙЛБАР: {customer_message}
ШААРДЛАГАТАЙ АРГА ХЭМЖЭЭ: Ажилтны анхаарал шаардлагатай
ХҮЛЭЭГДЭЖ БУЙ ХАРИУЛТ: Хэрэглэгчийн асуудлыг шийдэх"""
        
        print(f"✅ Энгийн дүгнэлт бэлэн: {simple_analysis[:100]}...")
        
        # Teams-ээр мэдээлэх
        success = send_teams_notification(
            conv_id,
            customer_message,
            customer_email,
            "Хэрэглэгчийн асуудлын дүгнэлт",
            simple_analysis
        )
        
        if success:
            print("✅ Асуудлыг амжилттай ажилтанд хуваарилав")
            return "👋 Би таны асуудлыг дүгнэж, ажилтанд дамжуулаа. Удахгүй ажилтан тантай холбогдоно.\n\n🕐 Түр хүлээнэ үү..."
        else:
            print("❌ Teams мэдээлэл илгээхэд алдаа")
            return "Уучлаарай, таны асуудлыг ажилтанд дамжуулахад алдаа гарлаа. Дахин оролдоно уу."
            
    except Exception as e:
        print(f"❌ Escalation алдаа: {e}")
        return "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."

def should_escalate_to_teams(thread_id, current_message):
    """Тухайн асуудлыг Teams-д илгээх хэрэгтэй эсэхийг шийдэх"""
    try:
        # OpenAI thread-с сүүлийн 20 мессежийг авах
        messages = client.beta.threads.messages.list(thread_id=thread_id, limit=20)
        
        # Хэрэглэгчийн мессежүүдийг цуглуулах
        user_messages = []
        for msg in reversed(messages.data):
            if msg.role == "user":
                content = ""
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        content += content_block.text.value
                if content.strip():
                    user_messages.append(content.strip())
        
        # Хэрэв анхны мессеж бол Teams-д илгээх
        if len(user_messages) <= 1:
            return True, "Анхны асуулт"
        
        # AI-аар шинэ асуудал мөн эсэхийг шалгах
        system_msg = (
            "Та бол чат дүн шинжилгээний мэргэжилтэн. "
            "Хэрэглэгчийн сүүлийн мессеж нь шинэ асуудал мөн эсэхийг тодорхойлно уу."
        )
        
        user_msg = f'''
Хэрэглэгчийн өмнөх мессежүүд:
{chr(10).join(user_messages[:-1])}

Одоогийн мессеж: "{current_message}"

Дараах аль нэгээр хариулна уу:
- "ШИН_АСУУДАЛ" - хэрэв одоогийн мессеж шинэ төрлийн асуудал бол
- "ҮРГЭЛЖЛЭЛ" - хэрэв өмнөх асуудлын үргэлжлэл, тодруулга бол
- "ДАХИН_АСУУЛТ" - хэрэв ижил асуудлыг дахин асууж байгаа бол
'''
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=50,
            temperature=0.1
        )
        
        analysis_result = response.choices[0].message.content.strip()
        
        if "ШИН_АСУУДАЛ" in analysis_result:
            return True, "Шинэ асуудал илрэв"
        else:
            return False, "Өмнөх асуудлын үргэлжлэл"
            
    except Exception as e:
        print(f"❌ Escalation шийдэх алдаа: {e}")
        # Алдаа гарвал анхны мессеж гэж үзэх
        return True, "Алдаа - анхны мессеж гэж үзэв"

if __name__ == "__main__":
    app.run(debug=True, port=5000)