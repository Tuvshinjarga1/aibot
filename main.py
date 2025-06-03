import os
import time
import requests
import threading
from datetime import datetime
from flask import Flask, request, jsonify

# ── Load .env ───────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── Logging ─────────────────────────────────────────────────────────────────────
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Environment variables ────────────────────────────────────────────────────────
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "").strip()
ASSISTANT_ID      = os.getenv("ASSISTANT_ID", "").strip()

CHATWOOT_API_KEY  = os.getenv("CHATWOOT_API_KEY", "").strip()
ACCOUNT_ID        = os.getenv("ACCOUNT_ID", "").strip()
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com").rstrip("/")

TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "").strip()
MAX_AI_RETRIES    = 2

# ── OpenAI client ────────────────────────────────────────────────────────────────
from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# ── Chatwoot helper functions ───────────────────────────────────────────────────

def send_to_chatwoot(conv_id: int, text: str) -> None:
    """
    Chatwoot руу outgoing (агентын) мессеж илгээх
    """
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "content": text,
        "message_type": "outgoing",
        "private": False
    }
    logger.info(f"📤 Sending to Chatwoot: {url}  payload={payload}")
    resp = requests.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    logger.info(f"📥 Chatwoot response: {resp.status_code}")

def get_conversation(conv_id: int) -> dict:
    """
    Conversation мэдээлэл авах
    """
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()

def update_conversation(conv_id: int, attrs: dict) -> None:
    """
    Conversation-ийн custom_attributes шинэчлэх
    """
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes"
    headers = {"api_access_token": CHATWOOT_API_KEY, "Content-Type": "application/json"}
    payload = {"custom_attributes": attrs}
    resp = requests.post(url, json=payload, headers=headers)
    resp.raise_for_status()

def get_contact(contact_id: int) -> dict:
    """
    Contact мэдээлэл авах
    """
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()

def update_contact(contact_id: int, attrs: dict) -> None:
    """
    Contact-ийн custom_attributes шинэчлэх
    """
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    headers = {"api_access_token": CHATWOOT_API_KEY, "Content-Type": "application/json"}
    payload = {"custom_attributes": attrs}
    resp = requests.put(url, json=payload, headers=headers)
    resp.raise_for_status()

# ── AI Assistant helper functions ──────────────────────────────────────────────

def clean_ai_response(response: str) -> str:
    """
    AI Assistant-аас ирсэн raw текстийг шаардлагагүй JSON үлдэгдэлгүй болгох.
    Энгийн хэлбэрт оруулна.
    """
    # Ямар ч JSON формат, илүүдэл мөр гарсан байвал арилгана
    import re, json

    # Хэрвээ төрөл JSON бол текст болгон буцаах
    try:
        if response.strip().startswith("{") and response.strip().endswith("}"):
            data = json.loads(response)
            # Тухайн танилцуулалт байсан бол энгийн мессеж болгож буцаана
            return "Таны хүсэлтийг хүлээн авлаа. Удахгүй хариулт өгөх болно."
    except json.JSONDecodeError:
        pass

    # JSON үлдэгдэл pattern устгах
    response = re.sub(r'\{[^}]*\}', '', response)
    # Илүүдэл хоосон мөр, зайг цэвэрлэх
    response = re.sub(r'\n\s*\n', '\n', response).strip()

    # Хэрвээ бичигдэл маш богино байвал default хариу гаргана
    if len(response) < 20:
        return "Таны хүсэлтийг хүлээн авлаа. Удахгүй хариулт өгөх болно."

    return response

def get_ai_response(thread_id: str, message_content: str, conv_id: int = None,
                    customer_email: str = None, retry_count: int = 0) -> str:
    """
    OpenAI Assistant-ээс хариулт авах:
    - Thread-д user мессеж нэмэх
    - run үүсгэн хариулт хүлээх
    - assistant-гийн reply-г цэвэрлэж буцаах
    """
    try:
        # Хэрэглэгчийн мессежийг thread-д нэмэх
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=message_content)

        # Assistant run үүсгэх
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)

        # Run бүрэн болох хүртэл хүлээх (max 30 секунд)
        max_wait = 30
        wait_count = 0
        while wait_count < max_wait:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status == "completed":
                break
            elif run_status.status in ["failed", "cancelled", "expired"]:
                # Хэрвээ эхний удаа бол Teams-д алдаа явуулна
                if retry_count == 0 and conv_id:
                    send_teams_notification(conv_id, message_content, customer_email,
                                            f"AI run статус алдаа: {run_status.status}", f"Run ID: {run.id}")
                return "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."
            time.sleep(1)
            wait_count += 1

        if wait_count >= max_wait:
            # Timeout тохиолдолд Teams-д мэдэгдэж болно
            if retry_count == 0 and conv_id:
                send_teams_notification(conv_id, message_content, customer_email,
                                        "AI хариулт timeout (30 секунд)", f"Run ID: {run.id}")
            return "Хариулахад хэт удаж байна. Дахин оролдоно уу."

        # assistant-гийн reply-г авах
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in messages.data:
            if msg.role == "assistant":
                reply = "".join([b.text.value for b in msg.content if hasattr(b, "text")])
                return clean_ai_response(reply)

        # Хариулт олдсонгүй бол
        if retry_count == 0 and conv_id:
            send_teams_notification(conv_id, message_content, customer_email,
                                    "AI хариулт олдсонгүй", f"Thread ID: {thread_id}")
        return "Хариулт олдсонгүй. Дахин оролдоно уу."

    except Exception as e:
        logger.error(f"AI хариулт авахад алдаа: {e}")
        if retry_count == 0 and conv_id:
            send_teams_notification(conv_id, message_content, customer_email,
                                    "AI системийн алдаа (Exception)", f"Exception: {e}")
        return "Уучлаарай, алдаа гарлаа. Дахин оролдоно уу."

def send_teams_notification(conv_id: int, customer_message: str, customer_email: str = None,
                            escalation_reason: str = "Хэрэглэгчийн асуудал", ai_analysis: str = None) -> bool:
    """
    Microsoft Teams webhook-д техникийн мэдээлэл илгээх (заавал биш)
    """
    if not TEAMS_WEBHOOK_URL:
        return False
    try:
        conv_url = f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
        error_summary = escalation_reason
        if ai_analysis:
            error_summary += f"\n\nAI анализ: {ai_analysis}"
        teams_payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.3",
                    "body": [
                        {"type": "TextBlock", "text": "📋 Хэрэглэгчийн дүгнэлт", "weight": "Bolder", "size": "Medium", "color": "Attention"},
                        {"type": "TextBlock", "text": "AI системтэй дүн шинжилгээ хийгдэж байна.", "wrap": True},
                        {"type": "FactSet", "facts": [
                            {"title": "Харилцагч:", "value": customer_email or "Тодорхойгүй"},
                            {"title": "Мессеж:", "value": customer_message[:300] + ("..." if len(customer_message) > 300 else "")},
                            {"title": "Цаг:", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                        ]}
                    ]
                }
            }]
        }
        if ai_analysis:
            teams_payload["attachments"][0]["content"]["body"].append({
                "type": "TextBlock", "text": "🤖 AI дүгнэлт:", "weight": "Bolder", "size": "Medium", "spacing": "Large"
            })
            teams_payload["attachments"][0]["content"]["body"].append({
                "type": "TextBlock", "text": ai_analysis, "wrap": True, "fontType": "Monospace", "color": "Good"
            })
        teams_payload["attachments"][0]["content"]["actions"] = [
            {"type": "Action.OpenUrl", "title": "Chatwoot дээр харах", "url": conv_url}
        ]
        resp = requests.post(TEAMS_WEBHOOK_URL, json=teams_payload)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Teams мэдээлэл илгээхэд алдаа: {e}")
        return False


# ── Flask Routes ────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook_handler():
    """
    Chatwoot webhook handler:
    - Зөвхөн “incoming” мессежэд хариу өгнө.
    - Баталгаажуулалт, RAG системгүй, энгийн AI Assistant-тай харилцана.
    """
    try:
        data = request.json or {}
        logger.info(f"🔄 Webhook ирлээ: {data.get('message_type', 'unknown')}")

        # Зөвхөн “incoming” мессеж боловсруулна
        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped - not incoming"}), 200

        # 1) conv_id болон мессежийн content
        conv_id = data.get("conversation", {}).get("id")
        message_content = (data.get("content") or "").strip()
        logger.info(f"📝 conv_id={conv_id}, content='{message_content}'")

        # 2) contact_id
        contact_id = None
        if data.get("sender") and data["sender"].get("id"):
            contact_id = data["sender"]["id"]

        if not conv_id or not contact_id:
            # Conversation эсвэл contact үүсээгүй бол алдаа
            logger.warning("❌ Conversation эсвэл Contact ID олдсонгүй")
            return jsonify({"status": "error - missing conv_id or contact_id"}), 400

        # 3) Conversation custom_attributes-аас thread_id авах эсэх
        conv = get_conversation(conv_id)
        conv_attrs = conv.get("custom_attributes", {})
        thread_key = f"openai_thread_{contact_id}"
        thread_id = conv_attrs.get(thread_key)

        # 4) Хэрвээ thread_id байхгүй бол шинэ thread үүсгэж, conversation-д хадгалах
        if not thread_id:
            logger.info("🧵 Шинэ thread үүсгэж байна...")
            thread = client.beta.threads.create()
            thread_id = thread.id
            update_conversation(conv_id, {thread_key: thread_id})
            logger.info(f"✅ Thread үүсгэлээ: {thread_id}")
        else:
            logger.info(f"✅ Одоо байгаа thread ашиглаж байна: {thread_id}")

        # 5) AI хариулт бэлтгэх (thread-д user мессеж оруулж, assistant run хүлээх)
        ai_response_text = None
        ai_success = False

        def run_ai_assistant():
            nonlocal ai_response_text, ai_success
            retry_count = 0
            while retry_count <= MAX_AI_RETRIES:
                resp = get_ai_response(thread_id, message_content, conv_id, None, retry_count)
                if not any(err in resp for err in ["алдаа гарлаа", "хэт удаж байна", "олдсонгүй"]):
                    ai_response_text = resp
                    ai_success = True
                    logger.info(f"✅ AI хариулт: {resp[:50]}...")
                    break
                retry_count += 1
                if retry_count <= MAX_AI_RETRIES:
                    logger.info(f"🔄 AI дахин оролдож байна... ({retry_count}/{MAX_AI_RETRIES})")
                    time.sleep(2)
            if not ai_success:
                logger.error("❌ AI бүх оролдлого бүтэлгүйтэв")

        ai_thread = threading.Thread(target=run_ai_assistant)
        ai_thread.start()
        ai_thread.join(timeout=30)

        logger.info(f"🔍 AI амжилттай: {ai_success}")

        # 6) Хариулт бэлдэх
        if ai_success:
            final_response = ai_response_text
            response_type = "AI Assistant"
        else:
            final_response = (
                "🚨 Уучлаарай, техникийн алдаа гарлаа. "
                "Таны асуултыг техникийн багт дамжуулсан. Удахгүй хариулт өгөх болно."
            )
            response_type = "Error - Escalated"
            # Хэрэв хүсвэл Teams рүү мэдээлж болно
            try:
                send_teams_notification(conv_id, message_content, None,
                                        "AI Assistant хариулт алдаатай", None)
            except Exception as e:
                logger.error(f"❌ Teams мэдээлэх алдаа: {e}")

        # 7) Chatwoot руу outgoing хариу илгээх
        send_to_chatwoot(conv_id, final_response)
        logger.info(f"✅ {response_type} хариулт илгээлээ: {final_response[:50]}...")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

if __name__ == "__main__":
    # debug=True бол алдаа гарсан үед дэлгэрэнгүй мэдээлэл харуулна
    app.run(host="0.0.0.0", port=5000, debug=True)
