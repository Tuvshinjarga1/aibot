import os
import time
import requests
import re
import jwt
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
from openai import OpenAI

# RAG системийн импорт нэмэх
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urldefrag
from dotenv import load_dotenv
import logging
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.chains import RetrievalQA
from langchain.schema import Document
from langchain_openai import OpenAI as LC_OpenAI
from langchain.prompts import PromptTemplate

# Load .env
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Орчны хувьсагчид
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
ASSISTANT_ID      = os.environ.get("ASSISTANT_ID", "")
CHATWOOT_API_KEY  = os.environ.get("CHATWOOT_API_KEY", "")
ACCOUNT_ID        = os.environ.get("ACCOUNT_ID", "")
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

# RAG системийн тохиргоо
DOCS_BASE_URL = os.environ.get("DOCS_BASE_URL", "https://docs.cloud.mn")
VECTOR_STORE_PATH = "docs_faiss_index"

# Email тохиргоо
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")

# Имэйл баталгаажуулалт идэвхтэй эсэхийг шалгах
EMAIL_VERIFICATION_ENABLED = bool(SENDER_EMAIL and SENDER_PASSWORD)

# Microsoft Teams тохиргоо
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")
MAX_AI_RETRIES = 2

# JWT тохиргоо
JWT_SECRET = os.environ.get("JWT_SECRET", "your-secret-key-here")
VERIFICATION_URL_BASE = os.environ.get("VERIFICATION_URL_BASE", "http://localhost:5000")

# OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Debug мэдээлэл хэвлэх
print("🔧 СИСТЕМИЙН ТОХИРГОО")
print(f"📧 Имэйл баталгаажуулалт: {'✅ Идэвхтэй' if EMAIL_VERIFICATION_ENABLED else '❌ Идэвхгүй'}")
print(f"🤖 OpenAI: {'✅ Тохируулсан' if OPENAI_API_KEY else '❌ Тохируулаагүй'}")
print(f"💬 Chatwoot: {'✅ Тохируулсан' if CHATWOOT_API_KEY else '❌ Тохируулаагүй'}")

if EMAIL_VERIFICATION_ENABLED:
    print(f"📧 SMTP: {SMTP_SERVER}:{SMTP_PORT}")
    print(f"📧 Илгээгч: {SENDER_EMAIL}")
else:
    print("⚠️ Имэйл баталгаажуулалт идэвхжүүлэхийн тулд .env файлд SENDER_EMAIL болон SENDER_PASSWORD тохируулна уу")

# =============== RAG СИСТЕМИЙН ФУНКЦУУД ===============

def crawl_docs(base_url: str) -> list:
    """Документ сайтаас мэдээлэл цуглуулах"""
    seen = set()
    to_visit = {base_url}
    docs = []
    
    logger.info(f"Starting to crawl docs from {base_url}")
    
    while to_visit and len(docs) < 100:  # 100 хуудас хязгаар
        url = to_visit.pop()
        if url in seen:
            continue
            
        seen.add(url)
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Title авах
            title = soup.find('title')
            title_text = title.get_text().strip() if title else "No title"
            
            # Main content авах - ихэвчлэн article, main, .content гэх мэт
            content_selectors = [
                'article', 'main', '[role="main"]',
                '.content', '.post-content', '.entry-content',
                '.documentation', '.docs-content', '#content'
            ]
            
            content_text = ""
            for selector in content_selectors:
                content = soup.select_one(selector)
                if content:
                    # Script, style tags арилгах
                    for script in content(["script", "style", "nav", "header", "footer"]):
                        script.decompose()
                    content_text = content.get_text()
                    break
            
            # Хэрэв content олдоогүй бол body ашиглах
            if not content_text:
                body = soup.find('body')
                if body:
                    for script in body(["script", "style", "nav", "header", "footer"]):
                        script.decompose()
                    content_text = body.get_text()
            
            # Текст цэвэрлэх
            content_text = re.sub(r'\s+', ' ', content_text).strip()
            
            if content_text and len(content_text) > 100:  # Хоосон хуудас алгасах
                docs.append(Document(
                    page_content=content_text,
                    metadata={
                        "source": url,
                        "title": title_text,
                        "length": len(content_text)
                    }
                ))
                logger.info(f"Crawled: {title_text} ({len(content_text)} chars)")
            
            # Шинэ холбоосууд олох
            links = soup.find_all('a', href=True)
            for link in links:
                href = link['href']
                full_url = urljoin(url, href)
                clean_url, _ = urldefrag(full_url)  # Fragment арилгах
                
                # Зөвхөн ижил домэйн
                if clean_url.startswith(base_url) and clean_url not in seen:
                    to_visit.add(clean_url)
                    
        except Exception as e:
            logger.error(f"Error crawling {url}: {str(e)}")
            continue
    
    logger.info(f"Crawling completed. Found {len(docs)} documents")
    return docs

def build_vectorstore():
    """Документуудаас vector store бүтээх"""
    try:
        logger.info("Building vector store from docs...")
        
        # Документ татах
        docs = crawl_docs(DOCS_BASE_URL)
        
        if not docs:
            logger.warning("No documents found to index")
            return None
        
        # Текст хуваах
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
        )
        
        splits = text_splitter.split_documents(docs)
        logger.info(f"Split documents into {len(splits)} chunks")
        
        # OpenAI embeddings
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        
        # FAISS vector store үүсгэх
        vectorstore = FAISS.from_documents(splits, embeddings)
        
        # Хадгалах
        vectorstore.save_local(VECTOR_STORE_PATH)
        logger.info(f"Vector store saved to {VECTOR_STORE_PATH}")
        
        return vectorstore
        
    except Exception as e:
        logger.error(f"Error building vector store: {str(e)}")
        return None

def load_vectorstore():
    """Хадгалсан vector store ачаалах"""
    try:
        if not os.path.exists(VECTOR_STORE_PATH):
            logger.info("Vector store not found. Building new one...")
            return build_vectorstore()
        
        logger.info("Loading existing vector store...")
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        vectorstore = FAISS.load_local(VECTOR_STORE_PATH, embeddings, allow_dangerous_deserialization=True)
        logger.info("Vector store loaded successfully")
        return vectorstore
        
    except Exception as e:
        logger.error(f"Error loading vector store: {str(e)}")
        return build_vectorstore()

# Custom prompt for RAG
CUSTOM_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template="""Та Cloud.mn-ийн тусламжийн систем бөгөөд доорх баримт материалын үндсэн дээр хэрэглэгчийн асуултанд хариулах ёстой.

Баримт материал:
{context}

Хэрэглэгчийн асуулт: {question}

Зөвлөмж:
1. Зөвхөн өгөгдсөн баримт материалын мэдээлэл ашиглана уу
2. Хэрэв баримт материалд хариулт байхгүй бол "Уучлаарай, энэ талаар баримт материалд мэдээлэл байхгүй байна" гэж хэлнэ үү
3. Товч, ойлгомжтой хариулт өгнө үү
4. Монгол хэлээр хариулна уу

Хариулт:"""
)

# Initialize RAG system
qa_chain = None
if OPENAI_API_KEY:
    try:
        vectorstore = load_vectorstore()
        if vectorstore:
            retriever = vectorstore.as_retriever(
                search_type="similarity",
                search_kwargs={"k": 5}
            )
            
            qa_chain = RetrievalQA.from_chain_type(
                llm=LC_OpenAI(
                    openai_api_key=OPENAI_API_KEY, 
                    temperature=0.1,
                    max_tokens=800,
                    model_name="gpt-3.5-turbo-instruct"
                ),
                chain_type="stuff",
                retriever=retriever,
                chain_type_kwargs={"prompt": CUSTOM_PROMPT},
                return_source_documents=True
            )
            logger.info("RAG system initialized successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize RAG system: {str(e)}")
        qa_chain = None

# =============== ЭНГИЙН ИМЭЙЛ ФУНКЦУУД ===============

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
        logger.info(f"Verifying JWT token: {token[:20]}...")
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        logger.info("JWT token verification successful")
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token has expired")
        print("❌ Токеның хугацаа дууссан")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {str(e)}")
        print(f"❌ Буруу токен: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error verifying JWT token: {str(e)}")
        print(f"❌ Токен шалгахад алдаа: {str(e)}")
        return None

def send_verification_email(email, token):
    """Баталгаажуулах имэйл илгээх - энгийн хувилбар"""
    if not EMAIL_VERIFICATION_ENABLED:
        print("❌ Имэйл баталгаажуулалт идэвхгүй байна")
        return False
        
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

Баярлалаа!
        """
        
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # SMTP серверт холбогдох
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Имэйл амжилттай илгээлээ: {email}")
        return True
        
    except Exception as e:
        print(f"❌ Имэйл илгээхэд алдаа: {e}")
        return False

# =============== CHATWOOT ФУНКЦУУД ===============

def get_contact(contact_id):
    """Contact мэдээлэл авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["payload"]["contact"]

def update_contact(contact_id, custom_attributes):
    """Contact дээр custom attribute шинэчлэх"""
    try:
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
        headers = {"api_access_token": CHATWOOT_API_KEY}
        payload = {"custom_attributes": custom_attributes}
        
        logger.info(f"Updating contact {contact_id} with attributes: {custom_attributes}")
        response = requests.put(url, json=payload, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"Chatwoot API error: {response.status_code} - {response.text}")
            
        response.raise_for_status()
        logger.info("Contact updated successfully")
        return response.json()
        
    except requests.exceptions.Timeout:
        logger.error("Timeout updating contact")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error updating contact: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error updating contact: {str(e)}")
        raise

def get_conversation(conv_id):
    """Conversation мэдээлэл авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["payload"]["conversation"]

def update_conversation(conv_id, custom_attributes):
    """Conversation дээр custom attribute шинэчлэх"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    payload = {"custom_attributes": custom_attributes}
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()

def send_to_chatwoot(conv_id, text):
    """Chatwoot руу мессеж илгээх"""
    try:
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
        headers = {"api_access_token": CHATWOOT_API_KEY}
        payload = {"content": text, "message_type": "outgoing"}
        
        logger.info(f"Sending message to conversation {conv_id}: {text[:50]}...")
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        
        if r.status_code != 200:
            logger.error(f"Chatwoot API error: {r.status_code} - {r.text}")
            
        r.raise_for_status()
        logger.info("Message sent successfully")
        return r.json()
        
    except requests.exceptions.Timeout:
        logger.error("Timeout sending message to Chatwoot")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error sending message: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error sending message: {str(e)}")
        raise

def get_ai_response(thread_id, message_content):
    """OpenAI Assistant-ээс энгийн хариулт авах"""
    if not client:
        return "OpenAI тохируулаагүй байна"
        
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

        # Assistant-ийн хариултыг авах
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

# =============== ROUTES ===============

@app.route("/verify", methods=["GET"])
def verify_email():
    """Имэйл баталгаажуулах endpoint"""
    try:
        token = request.args.get('token')
        if not token:
            logger.warning("Token parameter missing from verify request")
            return "Токен олдсонгүй!", 400
        
        logger.info(f"Attempting to verify email with token: {token[:20]}...")
        
        payload = verify_token(token)
        if not payload:
            logger.warning("Token verification failed - invalid or expired")
            return "Токен хүчингүй эсвэл хугацаа дууссан!", 400
        
        # Token-ийн дата шалгах
        conv_id = payload.get('conv_id')
        contact_id = payload.get('contact_id')
        email = payload.get('email')
        
        if not all([conv_id, contact_id, email]):
            logger.error(f"Invalid token payload: conv_id={conv_id}, contact_id={contact_id}, email={email}")
            return "Токен буруу форматтай байна!", 400
        
        logger.info(f"Token verified for email: {email}, conv_id: {conv_id}, contact_id: {contact_id}")
        
        # Chatwoot API key шалгах
        if not CHATWOOT_API_KEY or not ACCOUNT_ID:
            logger.error("Chatwoot configuration missing")
            return "Системийн тохиргооны алдаа. Техникийн дэмжлэгт хандана уу.", 500
        
        try:
            # Contact дээр баталгаажуулалтын мэдээлэл хадгалах
            logger.info(f"Updating contact {contact_id} verification status")
            update_contact(contact_id, {
                "email_verified": "true",
                "verified_email": email,
                "verification_date": datetime.utcnow().isoformat()
            })
            logger.info("Contact updated successfully")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Chatwoot API error when updating contact: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}, Response body: {e.response.text}")
            # Continue execution even if contact update fails
        except Exception as e:
            logger.error(f"Unexpected error updating contact: {e}")
            # Continue execution
        
        try:
            # Баталгаажуулах мессеж илгээх
            logger.info(f"Sending verification success message to conversation {conv_id}")
            send_to_chatwoot(conv_id, f"✅ Таны имэйл хаяг ({email}) амжилттай баталгаажлаа! Одоо та chatbot-той харилцаж болно.")
            logger.info("Verification message sent successfully")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Chatwoot API error when sending message: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}, Response body: {e.response.text}")
            # Continue to show success page even if message fails
        except Exception as e:
            logger.error(f"Unexpected error sending message: {e}")
            # Continue to show success page
        
        # Амжилттай баталгаажуулалтын хуудас харуулах
        logger.info("Displaying verification success page")
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Имэйл баталгаажлаа</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f5f5f5; }
                .container { max-width: 500px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                .success { color: #4CAF50; font-size: 24px; margin: 20px 0; }
                .info { color: #666; font-size: 16px; line-height: 1.5; }
                .email { background: #f0f0f0; padding: 10px; border-radius: 5px; font-family: monospace; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="success">✅ Амжилттай баталгаажлаа!</div>
                <div class="info">Таны имэйл хаяг:<br><span class="email">{{ email }}</span><br><br>баталгаажлаа. Одоо та chatbot-тойгоо харилцаж болно.</div>
            </div>
        </body>
        </html>
        """, email=email)
        
    except Exception as e:
        logger.error(f"Verification endpoint error: {str(e)}", exc_info=True)
        return f"Баталгаажуулахад алдаа гарлаа: {str(e)}", 500

@app.route("/webhook", methods=["POST"])
def webhook():
    """Энгийн Webhook Handler"""
    try:
        data = request.json
        print(f"🔄 Webhook хүлээн авлаа: {data.get('message_type', 'unknown')}")
        
        # Зөвхөн incoming мессеж боловсруулах
        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped"}), 200

        # Үндсэн мэдээлэл
        conv_id = data["conversation"]["id"]
        message_content = data.get("content", "").strip()
        contact_id = data.get("sender", {}).get("id")
        
        if not contact_id:
            send_to_chatwoot(conv_id, "❌ Хэрэглэгчийн мэдээлэл олдсонгүй.")
            return jsonify({"status": "error"}), 400

        print(f"📝 Conv: {conv_id}, Contact: {contact_id}, Message: '{message_content}'")

        # ========== ИМЭЙЛ БАТАЛГААЖУУЛАЛТ ШАЛГАХ ==========
        
        is_verified = False
        verified_email = ""
        
        if EMAIL_VERIFICATION_ENABLED:
            # Contact-ийн баталгаажуулалт шалгах
            try:
                contact = get_contact(contact_id)
                contact_attrs = contact.get("custom_attributes", {})
                is_verified = contact_attrs.get("email_verified") == "true"
                verified_email = contact_attrs.get("verified_email", "")
                print(f"✅ Баталгаажуулсан: {is_verified}, Имэйл: {verified_email}")
            except Exception as e:
                print(f"❌ Contact мэдээлэл авахад алдаа: {e}")
                is_verified = False
        else:
            # Имэйл баталгаажуулалт идэвхгүй бол шууд дамжуулах
            print("⚠️ Имэйл баталгаажуулалт идэвхгүй - шууд AI руу дамжуулж байна")
            is_verified = True
            verified_email = "no-verification@example.com"

        # ========== БАТАЛГААЖУУЛААГҮЙ БОЛ ИМЭЙЛ ШААРДАХ ==========
        
        if not is_verified:
            print("🚫 Баталгаажуулаагүй хэрэглэгч")
            
            if is_valid_email(message_content):
                # Зөв имэйл хүлээн авсан
                print(f"📧 Зөв имэйл хүлээн авлаа: {message_content}")
                
                token = generate_verification_token(message_content, conv_id, contact_id)
                
                if send_verification_email(message_content, token):
                    send_to_chatwoot(conv_id, 
                        f"📧 Таны имэйл хаяг ({message_content}) рүү баталгаажуулах линк илгээлээ.\n\n"
                        "Имэйлээ шалгаад линк дээр дарна уу. Линк 24 цагийн дараа хүчингүй болно.")
                else:
                    send_to_chatwoot(conv_id, 
                        "❌ Имэйл илгээхэд алдаа гарлаа. Техникийн дэмжлэгт хандана уу.")
            else:
                # Буруу имэйл формат
                send_to_chatwoot(conv_id, 
                    "👋 Сайн байна уу!\n\n"
                    "📧 Та эхлээд зөв имэйл хаягаа бичээд илгээнэ үү.\n"
                    "Жишээ: example@gmail.com")
            
            return jsonify({"status": "waiting_verification"}), 200

        # ========== БАТАЛГААЖУУЛСАН ХЭРЭГЛЭГЧ - AI-Д ДАМЖУУЛАХ ==========
        
        print("✅ Баталгаажуулсан хэрэглэгч - AI руу дамжуулж байна")
        
        # Thread мэдээлэл авах/үүсгэх
        try:
            conv = get_conversation(conv_id)
            conv_attrs = conv.get("custom_attributes", {})
            thread_key = f"openai_thread_{contact_id}"
            thread_id = conv_attrs.get(thread_key)
            
            # Thread шинээр үүсгэх
            if not thread_id:
                print("🧵 Шинэ thread үүсгэж байна...")
                thread = client.beta.threads.create()
                thread_id = thread.id
                update_conversation(conv_id, {thread_key: thread_id})
                print(f"✅ Thread үүсгэлээ: {thread_id}")
        except Exception as e:
            print(f"❌ Thread үүсгэхэд алдаа: {e}")
            send_to_chatwoot(conv_id, "❌ Техникийн алдаа гарлаа. Дахин оролдоно уу.")
            return jsonify({"status": "error"}), 500

        # AI хариулт авах
        try:
            ai_response = get_ai_response(thread_id, message_content)
            send_to_chatwoot(conv_id, ai_response)
            print(f"✅ AI хариулт илгээлээ: {ai_response[:50]}...")
        except Exception as e:
            print(f"❌ AI хариулт авахад алдаа: {e}")
            send_to_chatwoot(conv_id, "❌ Уучлаарай, алдаа гарлаа. Дахин оролдоно уу.")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

@app.route("/health", methods=["GET"])
def health():
    """Системийн health check"""
    status = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "openai": client is not None and bool(OPENAI_API_KEY),
            "chatwoot": bool(CHATWOOT_API_KEY and ACCOUNT_ID),
            "email": EMAIL_VERIFICATION_ENABLED,
            "rag": qa_chain is not None
        }
    }
    
    # Нийт статус шалгах
    all_ok = all(status["components"].values())
    if not all_ok:
        status["status"] = "warning"
        
    return jsonify(status), 200 if all_ok else 206

@app.route("/", methods=["GET"])
def home():
    """Үндсэн хуудас"""
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>AI Assistant Системийн Төв</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; background: #f5f5f5; }
            .container { background: white; padding: 40px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #333; text-align: center; }
            .status { margin: 20px 0; }
            .status-item { padding: 10px; margin: 5px 0; border-radius: 5px; }
            .status-ok { background: #d4edda; color: #155724; }
            .status-error { background: #f8d7da; color: #721c24; }
            .info { background: #e7f3ff; padding: 20px; border-radius: 5px; margin: 20px 0; }
            code { background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 AI Assistant Системийн Төв</h1>
            
            <div class="status">
                <h3>Системийн статус:</h3>
                <div class="status-item {{ 'status-ok' if components.openai else 'status-error' }}">
                    OpenAI: {{ '✅ Идэвхтэй' if components.openai else '❌ Тохируулаагүй' }}
                </div>
                <div class="status-item {{ 'status-ok' if components.chatwoot else 'status-error' }}">
                    Chatwoot: {{ '✅ Идэвхтэй' if components.chatwoot else '❌ Тохируулаагүй' }}
                </div>
                <div class="status-item {{ 'status-ok' if components.email else 'status-error' }}">
                    Имэйл баталгаажуулалт: {{ '✅ Идэвхтэй' if components.email else '❌ Идэвхгүй' }}
                </div>
                <div class="status-item {{ 'status-ok' if components.rag else 'status-error' }}">
                    RAG систем: {{ '✅ Идэвхтэй' if components.rag else '❌ Идэвхгүй' }}
                </div>
            </div>
            
            <div class="info">
                <h3>📋 Ашиглах заавар:</h3>
                <p><strong>Webhook URL:</strong> <code>{{ request.url_root }}webhook</code></p>
                <p><strong>Health Check:</strong> <code>{{ request.url_root }}health</code></p>
                <p><strong>Системийн тест:</strong> <code>{{ request.url_root }}test</code></p>
                <p><strong>Имэйл баталгаажуулалт:</strong> <code>{{ request.url_root }}verify?token=...</code></p>
            </div>
            
            <div class="info">
                <h3>⚙️ Тохиргооны зөвлөмж:</h3>
                <ul>
                    <li><strong>.env файл:</strong> Бүх API key болон тохиргоонуудыг .env файлд тохируулна уу</li>
                    <li><strong>Gmail:</strong> Gmail ашиглах бол App Password үүсгэх шаардлагатай</li>
                    <li><strong>Webhook:</strong> Chatwoot дээр webhook URL тохируулах шаардлагатай</li>
                </ul>
            </div>
        </div>
    </body>
    </html>
    """, **health().get_json())

@app.route("/test", methods=["GET"])
def test_system():
    """Системийн үндсэн функцуудыг тест хийх"""
    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "tests": {}
    }
    
    # JWT тест
    try:
        test_payload = {
            'email': 'test@example.com',
            'conv_id': 123,
            'contact_id': 456,
            'exp': datetime.utcnow() + timedelta(hours=1)
        }
        test_token = jwt.encode(test_payload, JWT_SECRET, algorithm='HS256')
        decoded = verify_token(test_token)
        results["tests"]["jwt"] = {
            "status": "✅ Амжилттай" if decoded else "❌ Алдаа",
            "details": "JWT токен үүсгэх/шалгах" 
        }
    except Exception as e:
        results["tests"]["jwt"] = {
            "status": "❌ Алдаа", 
            "details": f"JWT алдаа: {str(e)}"
        }
    
    # Environment variables тест  
    env_vars = {
        "CHATWOOT_API_KEY": bool(CHATWOOT_API_KEY),
        "ACCOUNT_ID": bool(ACCOUNT_ID),
        "OPENAI_API_KEY": bool(OPENAI_API_KEY),
        "JWT_SECRET": bool(JWT_SECRET)
    }
    results["tests"]["environment"] = {
        "status": "✅ Амжилттай" if all(env_vars.values()) else "⚠️ Дутуу",
        "details": env_vars
    }
    
    # Chatwoot API тест (хэрэв API key байвал)
    if CHATWOOT_API_KEY and ACCOUNT_ID:
        try:
            url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}"
            headers = {"api_access_token": CHATWOOT_API_KEY}
            response = requests.get(url, headers=headers, timeout=10)
            results["tests"]["chatwoot"] = {
                "status": "✅ Амжилттай" if response.status_code == 200 else f"❌ Алдаа ({response.status_code})",
                "details": f"Chatwoot API холболт - Account: {ACCOUNT_ID}"
            }
        except Exception as e:
            results["tests"]["chatwoot"] = {
                "status": "❌ Алдаа",
                "details": f"Chatwoot API алдаа: {str(e)}"
            }
    else:
        results["tests"]["chatwoot"] = {
            "status": "⚠️ Тохируулаагүй",
            "details": "Chatwoot API key эсвэл Account ID дутуу"
        }
    
    return jsonify(results)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)