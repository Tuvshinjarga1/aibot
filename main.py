import os
import re
import jwt
import smtplib
import requests

from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
from openai import OpenAI
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)

# ─── Конфиг ───────────────────────────────────────────────────────────
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID      = os.environ["ASSISTANT_ID"]
CHATWOOT_API_KEY  = os.environ["CHATWOOT_API_KEY"]
ACCOUNT_ID        = os.environ["ACCOUNT_ID"]
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

SMTP_SERVER       = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT         = int(os.environ.get("SMTP_PORT", "587"))
SENDER_EMAIL      = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD   = os.environ["SENDER_PASSWORD"]

TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")
JWT_SECRET        = os.environ.get("JWT_SECRET", "your-secret-key-here")
VERIF_URL_BASE    = os.environ.get("VERIFICATION_URL_BASE", "http://localhost:5000")

# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)


# ─── Хэлт хэрэглэх функцууд ────────────────────────────────────────
def is_valid_email(email: str) -> bool:
    return re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email) is not None

def gen_token(email, conv_id, contact_id):
    payload = {
        'email': email,
        'conv_id': conv_id,
        'contact_id': contact_id,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except Exception:
        return None

def send_email(to_email, token):
    link = f"{VERIF_URL_BASE}/verify?token={token}"
    body = f"Имэйл баталгаажуулах линк: {link}\n(24 цагийн хүчинтэй)"
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = "Имэйл баталгаажуулна уу"
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    try:
        smtp = smtplib.SMTP(SMTP_SERVER, SMTP_PORT); smtp.starttls()
        smtp.login(SENDER_EMAIL, SENDER_PASSWORD)
        smtp.send_message(msg); smtp.quit()
        return True
    except:
        return False

def send_to_chatwoot(conv_id, text):
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    payload = {"content": text, "message_type": "outgoing"}
    requests.post(url, json=payload, headers=headers).raise_for_status()

def get_ai_response(thread_id, user_text):
    # нэмэлт retry logic-гүй, энгийн хариулт авах
    client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_text)
    run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)
    for _ in range(30):
        status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if status.status == "completed":
            break
        time.sleep(1)
    msgs = client.beta.threads.messages.list(thread_id=thread_id)
    for m in msgs.data:
        if m.role == "assistant":
            return "".join(getattr(c, 'text').value for c in m.content if hasattr(c, 'text'))
    return "Хариулт олдсонгүй."

def clean_ai_response(txt: str) -> str:
    txt = txt.strip()
    if txt.startswith('{') and txt.endswith('}'):
        try:
            obj = __import__('json').loads(txt)
            for key in ('clarification_question','message'):
                if key in obj:
                    return obj[key]
        except:
            pass
    return txt


# ─── Route-ууд ──────────────────────────────────────────────────────
@app.route("/verify", methods=["GET"])
def verify_email():
    token = request.args.get("token")
    data = verify_token(token) if token else None
    if not data:
        return "❌ Токен хүчин төгөлдөргүй!", 400

    # Contact-ийг verified болгох
    import requests as r
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{data['contact_id']}"
    r.put(url, json={"custom_attributes": {
        "email_verified":"1",
        "verified_email": data['email'],
        "verification_date": datetime.utcnow().isoformat()
    }}, headers={"api_access_token": CHATWOOT_API_KEY})

    # Хэрэв conv_id байгаа бол Chatwoot руу мессеж явуулах
    if data.get('conv_id'):
        send_to_chatwoot(data['conv_id'], f"✅ Таны имэйл ({data['email']}) баталгаажлаа!")

    return render_template_string("""
    <h2 style="color:green;">✅ Амжилттай баталгаажууллаа</h2>
    <p>Имэйл: {{email}}</p>
    """, email=data['email'])


@app.route("/webhook", methods=["POST"])
def webhook():
    d = request.json
    if d.get("message_type")!="incoming":
        return jsonify(status="skipped"),200

    conv_id = d["conversation"]["id"]
    text    = d.get("content","").strip()
    contact = d.get("sender",{}).get("id")
    if not contact:
        return jsonify(status="no_contact"),400

    # Баталгаажуулалт шалгах
    vld = False
    meta = d.get("conversation",{}).get("meta",{}).get("sender",{}).get("custom_attributes",{})
    if str(meta.get("email_verified","")).lower() in ("1","true","yes"):
        vld=True
    else:
        # API-аар дахин шалгах
        resp = requests.get(
            f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact}",
            headers={"api_access_token":CHATWOOT_API_KEY}
        ).json()
        if str(resp.get("custom_attributes",{}).get("email_verified","")).lower() in ("1","true","yes"):
            vld=True

    if not vld:
        # Зар сурталчилгаа: хэрвээ текст бол э-мэйл гэж ойлгоод явуулах
        if is_valid_email(text):
            token = gen_token(text, conv_id, contact)
            if send_email(text, token):
                send_to_chatwoot(conv_id, f"📧 Баталгаажуулах линк илгээлээ: шалгаарай ({text})")
            else:
                send_to_chatwoot(conv_id, "❌ Имэйл илгээхэд алдаа гарлаа.")
        else:
            send_to_chatwoot(conv_id,
                "👋 Эхлээд имэйл баталгаажуулна уу!\n"
                "Зөв имэйл бичиж явуулна: example@gmail.com"
            )
        return jsonify(status="awaiting_verification"),200

    # AI chatbot-хариулт
    conv = requests.get(
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}",
        headers={"api_access_token":CHATWOOT_API_KEY}
    ).json()
    thread_key = f"openai_thread_{contact}"
    thread_id  = conv.get("custom_attributes",{}).get(thread_key)

    if not thread_id:
        th = client.beta.threads.create()
        thread_id = th.id
        requests.post(
            f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes",
            json={"custom_attributes":{thread_key:thread_id}},
            headers={"api_access_token":CHATWOOT_API_KEY}
        )

    ai = get_ai_response(thread_id, text)
    ai = clean_ai_response(ai)
    send_to_chatwoot(conv_id, ai)
    return jsonify(status="success"),200


@app.route("/test-teams", methods=["GET"])
def test_teams():
    msg = {"text":"✅ Test мессеж ирлээ!"}
    requests.post(TEAMS_WEBHOOK_URL, json=msg)
    return jsonify(status="ok"),200


if __name__=="__main__":
    app.run(debug=True, port=5000)
