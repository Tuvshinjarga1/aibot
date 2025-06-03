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
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID      = os.environ["ASSISTANT_ID"]
CHATWOOT_API_KEY  = os.environ["CHATWOOT_API_KEY"]
ACCOUNT_ID        = os.environ["ACCOUNT_ID"]
CHATWOOT_BASE_URL = "https://app.chatwoot.com"

# RAG системийн тохиргоо
DOCS_BASE_URL = os.environ.get("DOCS_BASE_URL", "https://docs.cloud.mn")
VECTOR_STORE_PATH = "docs_faiss_index"

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

# =============== RAG СИСТЕМИЙН ФУНКЦУУД ===============

def crawl_docs(base_url: str) -> list:
    """Документ сайтаас мэдээлэл цуглуулах"""
    seen = set()
    to_visit = {base_url}
    docs = []
    
    logger.info(f"Starting to crawl docs from {base_url}")
    
    while to_visit:
        url = to_visit.pop()
        url = urldefrag(url).url
        if url in seen or not url.startswith(base_url):
            continue
        seen.add(url)
        
        try:
            logger.info(f"Crawling: {url}")
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch {url}: {resp.status_code}")
                continue
                
            soup = BeautifulSoup(resp.text, "lxml")
            
            # Better content extraction - try multiple selectors
            content = (
                soup.select_one("article") or 
                soup.select_one(".content") or
                soup.select_one("main") or
                soup.select_one(".markdown") or
                soup.select_one("#main-content")
            )
            
            if content:
                # Remove navigation, footer, header elements
                for unwanted in content.select("nav, footer, header, .nav, .footer, .header"):
                    unwanted.decompose()
                
                text = content.get_text(separator="\n").strip()
                if text and len(text) > 50:  # Filter out very short content
                    # Get page title for better context
                    title = soup.select_one("title")
                    title_text = title.get_text().strip() if title else ""
                    
                    docs.append({
                        "url": url, 
                        "text": text,
                        "title": title_text
                    })
                    logger.info(f"Extracted content from {url} - {len(text)} characters")
                    
            # Find more links
            for a in soup.find_all("a", href=True):
                link = urljoin(url, a["href"])
                if link.startswith(base_url) and "#" not in link:  # Avoid anchor links
                    to_visit.add(link)
                    
        except Exception as e:
            logger.error(f"Error crawling {url}: {str(e)}")
            continue
            
    logger.info(f"Crawling completed. Found {len(docs)} documents")
    return docs

def chunk_documents(documents: list) -> list:
    """Документуудыг жижиг хэсэгт хуваах"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,  # Increased chunk size for better content
        chunk_overlap=100,  # Increased overlap for better context
        separators=["\n\n", "\n", ".", "!", "?", ",", " ", ""]
    )
    
    chunks = []
    for doc in documents:
        text_chunks = splitter.split_text(doc["text"])
        for i, chunk in enumerate(text_chunks):
            # Create proper Document objects with metadata
            doc_obj = Document(
                page_content=chunk,
                metadata={
                    "source": doc["url"],
                    "title": doc.get("title", ""),
                    "chunk_id": i
                }
            )
            chunks.append(doc_obj)
    
    logger.info(f"Created {len(chunks)} chunks from {len(documents)} documents")
    return chunks

def load_vectorstore():
    """Vector store ачаалах эсвэл шинээр үүсгэх"""
    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    
    if os.path.exists(VECTOR_STORE_PATH):
        logger.info("Loading existing vector store...")
        return FAISS.load_local(VECTOR_STORE_PATH, embeddings, allow_dangerous_deserialization=True)
    
    logger.info("Creating new vector store...")
    docs = crawl_docs(DOCS_BASE_URL)
    
    if not docs:
        raise ValueError("No documents found during crawling")
    
    chunks = chunk_documents(docs)
    vs = FAISS.from_documents(chunks, embeddings)
    vs.save_local(VECTOR_STORE_PATH)
    logger.info("Vector store created and saved")
    return vs

# Custom prompt for CloudMN docs
CUSTOM_PROMPT = PromptTemplate(
    template="""CloudMN техникийн туслах. Доорх мэдээллээр хариулна уу:

Мэдээлэл: {context}

Асуулт: {question}

Хариулт (монгол хэлээр, товч бөгөөд тодорхой):""",
    input_variables=["context", "question"]
)

def search_docs_with_rag(question: str) -> dict:
    """RAG ашиглан документаас хариулт хайх"""
    try:
        if not qa_chain:
            return {
                "answer": "Документ хайлтын систем бэлэн биш байна.",
                "sources": []
            }
        
        # Get answer with source documents
        result = qa_chain.invoke({"query": question})
        answer = result["result"]
        sources = result.get("source_documents", [])
        
        # Format response with sources
        response = {
            "answer": answer,
            "sources": []
        }
        
        # Add unique sources (limit to 3)
        seen_sources = set()
        for doc in sources[:3]:  # Limit sources
            source_url = doc.metadata.get("source", "")
            if source_url and source_url not in seen_sources:
                seen_sources.add(source_url)
                response["sources"].append({
                    "url": source_url,
                    "title": doc.metadata.get("title", "")
                })
        
        return response
        
    except Exception as e:
        logger.error(f"RAG хайлтанд алдаа: {str(e)}")
        return {
            "answer": f"Документ хайлтанд алдаа гарлаа: {str(e)}",
            "sources": []
        }

# Initialize RAG system
try:
    vectorstore = load_vectorstore()
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

# =============== CHATWOOT ФУНКЦУУД ===============

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
        
        print(f"📧 Имэйл илгээж байна: {email}")
        print(f"🔗 Verification URL: {verification_url}")
        print(f"📮 SMTP Server: {SMTP_SERVER}:{SMTP_PORT}")
        print(f"👤 Sender Email: {SENDER_EMAIL}")
        
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
        
        print("🔌 SMTP серверт холбогдож байна...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        print("🔐 SMTP authentication хийж байна...")
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        print("📤 Имэйл илгээж байна...")
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Имэйл амжилттай илгээлээ: {email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ SMTP нэвтрэх алдаа: {e}")
        print("💡 Gmail App Password зөв эсэхийг шалгана уу")
        return False
    except smtplib.SMTPRecipientsRefused as e:
        print(f"❌ Хүлээн авагчийн имэйл буруу: {e}")
        return False
    except smtplib.SMTPServerDisconnected as e:
        print(f"❌ SMTP сервер салсан: {e}")
        return False
    except smtplib.SMTPException as e:
        print(f"❌ SMTP алдаа: {e}")
        return False
    except Exception as e:
        print(f"❌ Имэйл илгээхэд алдаа: {e}")
        print(f"❌ Алдааны төрөл: {type(e).__name__}")
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

def analyze_customer_issue(thread_id, current_message, customer_email=None):
    """AI ашиглан хэрэглэгчийн бүх чат түүхийг дүгнэж, comprehensive мэдээлэл өгөх"""
    try:
        # OpenAI thread-с сүүлийн 10 мессежийг л авах (performance сайжруулах)
        messages = client.beta.threads.messages.list(thread_id=thread_id, limit=10)
        
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
                    conversation_history.append(f"AI: {content.strip()[:100]}...")  # Хязгаарлах
        
        # Хэрэв чат түүх хоосон бол зөвхөн одоогийн мессежээр дүгнэх
        if not conversation_history:
            conversation_history = [f"Хэрэглэгч: {current_message}"]
        
        # Conversation түүхийг string болгох (сүүлийн 5 мессеж)
        chat_history = "\n".join(conversation_history[-5:])  # Хязгаарлах
        
        # Илүү тодорхой system prompt
        system_msg = (
            "Та бол дэмжлэгийн мэргэжилтэн. "
            "Хэрэглэгчийн бүх чат түүхийг харж, асуудлыг иж бүрэн дүгнэж өгнө үү. "
            "Хэрэв олон асуудал байвал гол асуудлыг тодорхойлж фокуслана уу."
        )

        # Богино user prompt
        user_msg = f'''Хэрэглэгчийн чат түүх:
{chat_history}

Одоогийн мессеж: "{current_message}"

Дараах форматаар товч дүгнэлт өгнө үү:

АСУУДЛЫН ТӨРӨЛ: [Техникийн/Худалдааны/Мэдээллийн/Гомдол]
ЯАРАЛТАЙ БАЙДАЛ: [Өндөр/Дунд/Бага] 
ТОВЧ ТАЙЛБАР: [1 өгүүлбэрээр]
ШААРДЛАГАТАЙ АРГА ХЭМЖЭЭ: [Товч]'''

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # gpt-4-ээс хурдан
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=200,  # Хязгаарлах
            temperature=0.2,
            timeout=15  # 15 секундын timeout
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"❌ Асуудал дүгнэхэд алдаа: {e}")
        # Fallback дүгнэлт
        return f"""АСУУДЛЫН ТӨРӨЛ: Тодорхойгүй
ЯАРАЛТАЙ БАЙДАЛ: Дунд
ТОВЧ ТАЙЛБАР: {current_message[:100]}
ШААРДЛАГАТАЙ АРГА ХЭМЖЭЭ: Ажилтны анхаарал шаардлагатай"""

def clean_ai_response(response: str) -> str:
    """AI хариултыг цэвэрлэх - JSON форматыг арилгах"""
    try:
        # JSON pattern олох
        import json
        
        # Хэрэв JSON объект байвал арилгах
        if response.strip().startswith('{') and response.strip().endswith('}'):
            try:
                # JSON parse хийж үзэх
                json_data = json.loads(response)
                
                # Хэрэв email, issue, details гэх мэт key-үүд байвал энгийн текст болгох
                if isinstance(json_data, dict):
                    if "email" in json_data or "issue" in json_data:
                        # JSON-ээс хэрэглэгчид ойлгомжтой мэдээлэл гаргах
                        clean_text = "Таны хүсэлтийг техникийн дэмжлэгийн багт дамжуулаа. "
                        clean_text += "Удахгүй асуудлыг шийдэж, танд хариулт өгөх болно."
                        return clean_text
            except json.JSONDecodeError:
                pass
        
        # JSON pattern-уудыг арилгах
        import re
        
        # {"email": "...", "issue": "...", "details": "..."} гэх мэт pattern арилгах
        json_pattern = r'\{[^}]*"email"[^}]*\}'
        response = re.sub(json_pattern, '', response)
        
        # Илүүдэл мөр, хоосон зай арилгах
        response = re.sub(r'\n\s*\n', '\n', response)
        response = response.strip()
        
        # Хэрэв хариулт хэт богино болсон бол default мессеж
        if len(response) < 20:
            return "Таны хүсэлтийг хүлээн авлаа. Удахгүй хариулт өгөх болно."
        
        return response
        
    except Exception as e:
        print(f"❌ AI хариулт цэвэрлэхэд алдаа: {e}")
        return response  # Алдаа гарвал анхны хариултыг буцаах

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
                
                # AI хариултыг цэвэрлэх - JSON форматыг арилгах
                cleaned_reply = clean_ai_response(reply)
                return cleaned_reply

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

        # ========== RAG болон AI ASSISTANT ЗЭРЭГ АЖИЛЛУУЛАХ ==========
        print("🚀 RAG болон AI Assistant-г зэрэг ажиллуулж байна...")
        
        # Thread мэдээлэл бэлтгэх
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
        
        # Хариултуудыг хадгалах хувьсагчид
        rag_response = {"answer": None, "sources": [], "success": False}
        ai_response_text = None
        ai_success = False
        
        # RAG функц
        def run_rag():
            nonlocal rag_response
            try:
                print("📚 RAG системээр хайж байна...")
                result = search_docs_with_rag(message_content)
                if result["answer"] and "алдаа гарлаа" not in result["answer"]:
                    rag_response = {
                        "answer": result["answer"],
                        "sources": result["sources"],
                        "success": True
                    }
                    print(f"✅ RAG амжилттай: {result['answer'][:50]}...")
                else:
                    print("❌ RAG хариулт олдсонгүй")
            except Exception as e:
                print(f"❌ RAG алдаа: {e}")
        
        # AI Assistant функц
        def run_ai_assistant():
            nonlocal ai_response_text, ai_success
            try:
                print("🤖 AI Assistant ажиллаж байна...")
                retry_count = 0
                while retry_count <= MAX_AI_RETRIES:
                    response = get_ai_response(thread_id, message_content, conv_id, verified_email, retry_count)
                    
                    # Хэрэв алдаатай хариулт биш бол амжилттай
                    if not any(error_phrase in response for error_phrase in [
                        "алдаа гарлаа", "хэт удаж байна", "олдсонгүй"
                    ]):
                        ai_response_text = response
                        ai_success = True
                        print(f"✅ AI Assistant амжилттай: {response[:50]}...")
                        break
                        
                    retry_count += 1
                    if retry_count <= MAX_AI_RETRIES:
                        print(f"🔄 AI дахин оролдож байна... ({retry_count}/{MAX_AI_RETRIES})")
                        time.sleep(2)
                
                if not ai_success:
                    print("❌ AI Assistant бүх оролдлого бүтэлгүйтэв")
                    
            except Exception as e:
                print(f"❌ AI Assistant алдаа: {e}")
        
        # Хоёр системийг зэрэг ажиллуулах
        rag_thread = threading.Thread(target=run_rag)
        ai_thread = threading.Thread(target=run_ai_assistant)
        
        # Thread эхлүүлэх
        rag_thread.start()
        ai_thread.start()
        
        # Хоёуланг нь дуусахыг хүлээх (максимум 45 секунд)
        rag_thread.join(timeout=30)
        ai_thread.join(timeout=30)
        
        print(f"🔍 Үр дүн: RAG={rag_response['success']}, AI={ai_success}")
        
        # ========== ХАРИУЛТУУДЫГ НЭГТГЭХ ==========
        final_response = ""
        response_type = ""
        
        if rag_response["success"] and ai_success:
            # Хоёулаа амжилттай бол нэгтгэх
            print("🎯 Хоёр систем амжилттай - хариултуудыг нэгтгэж байна")
            
            final_response = f"📚 **Документаас олсон мэдээлэл:**\n{rag_response['answer']}\n\n"
            final_response += f"🤖 **AI туслахын нэмэлт зөвлөгөө:**\n{ai_response_text}"
            
            # RAG sources нэмэх
            if rag_response["sources"]:
                final_response += "\n\n📖 **Холбогдох документууд:**\n"
                for i, source in enumerate(rag_response["sources"], 1):
                    title = source.get("title", "Документ")
                    url = source.get("url", "")
                    final_response += f"{i}. [{title}]({url})\n"
            
            response_type = "RAG + AI Assistant"
            
        elif rag_response["success"]:
            # Зөвхөн RAG амжилттай
            print("📚 Зөвхөн RAG амжилттай")
            
            final_response = rag_response["answer"]
            
            # RAG sources нэмэх
            if rag_response["sources"]:
                final_response += "\n\n📚 **Холбогдох документууд:**\n"
                for i, source in enumerate(rag_response["sources"], 1):
                    title = source.get("title", "Документ")
                    url = source.get("url", "")
                    final_response += f"{i}. [{title}]({url})\n"
            
            response_type = "RAG"
            
        elif ai_success:
            # Зөвхөн AI Assistant амжилттай
            print("🤖 Зөвхөн AI Assistant амжилттай")
            final_response = ai_response_text
            response_type = "AI Assistant"
            
        else:
            # Хоёулаа бүтэлгүйтэв
            print("❌ Хоёр систем бүтэлгүйтэв - ажилтанд хуваарилж байна")
            
            send_teams_notification(
                conv_id, 
                message_content, 
                verified_email, 
                "RAG болон AI Assistant хоёулаа бүтэлгүйтэв",
                f"Thread ID: {thread_id}, Хоёр систем алдаа гаргалаа"
            )
            
            final_response = (
                "🚨 Уучлаарай, техникийн асуудал гарлаа.\n\n"
                "Би таны асуултыг техникийн багт дамжуулаа. Удахгүй асуудлыг шийдэж, танд хариулт өгөх болно.\n\n"
                "🕐 Түр хүлээнэ үү..."
            )
            response_type = "Error - Escalated"
        
        # ========== ХАРИУЛТ ИЛГЭЭХ ==========
        # Chatwoot руу илгээх
        send_to_chatwoot(conv_id, final_response)
        print(f"✅ {response_type} хариулт илгээлээ: {final_response[:50]}...")
        
        # Teams мэдээлэх логик - зөвхөн шинэ асуудал эсвэл техникийн асуудал үед
        try:
            # Хэрэв хоёулаа амжилттай бол Teams-д мэдээлэх хэрэггүй
            if not (rag_response["success"] and ai_success):
                # Escalation шаардлагатай эсэхийг шалгах
                should_escalate, escalation_reason = should_escalate_to_teams(thread_id, message_content)
                
                if should_escalate:
                    # AI дүгнэлт хийх
                    ai_analysis = analyze_customer_issue(thread_id, message_content, verified_email)
                    
                    # Teams мэдээлэх
                    send_teams_notification(
                        conv_id, 
                        message_content, 
                        verified_email, 
                        escalation_reason,
                        ai_analysis
                    )
                    print(f"📢 Teams мэдээлэл илгээлээ: {escalation_reason}")
        except Exception as e:
            print(f"❌ Teams мэдээлэх алдаа: {e}")
        
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {str(e)}"}), 500

@app.route("/docs-search", methods=["POST"])
def docs_search():
    """RAG системээр документ хайх тусдаа endpoint"""
    try:
        if not qa_chain:
            return jsonify({"error": "RAG систем бэлэн биш байна"}), 500
            
        data = request.json
        if not data:
            return jsonify({"error": "JSON өгөгдөл байхгүй"}), 400
            
        question = data.get("question", "").strip()
        if not question:
            return jsonify({"error": "Асуулт байхгүй байна"}), 400
            
        logger.info(f"RAG хайлт: {question}")
        
        # RAG хайлт хийх
        result = search_docs_with_rag(question)
        
        # Response форматлах
        response = {
            "question": question,
            "answer": result["answer"],
            "sources": result["sources"],
            "timestamp": datetime.utcnow().isoformat()
        }
        
        logger.info(f"RAG хариулт: {len(result['sources'])} sources олдлоо")
        return jsonify(response), 200
        
    except Exception as e:
        logger.error(f"RAG endpoint алдаа: {str(e)}")
        return jsonify({"error": f"Системийн алдаа: {str(e)}"}), 500

@app.route("/health", methods=["GET"])
def health():
    """Системийн health check"""
    status = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "rag_system": qa_chain is not None,
            "openai_client": client is not None,
            "teams_webhook": TEAMS_WEBHOOK_URL is not None,
            "email_smtp": SENDER_EMAIL is not None and SENDER_PASSWORD is not None,
            "chatwoot_api": CHATWOOT_API_KEY is not None and ACCOUNT_ID is not None
        },
        "configuration": {
            "smtp_server": f"{SMTP_SERVER}:{SMTP_PORT}",
            "sender_email": SENDER_EMAIL if SENDER_EMAIL else "❌ Тохируулаагүй",
            "sender_password_set": "✅ Тохируулсан" if SENDER_PASSWORD else "❌ Тохируулаагүй",
            "verification_url_base": VERIFICATION_URL_BASE,
            "jwt_secret_set": "✅ Тохируулсан" if JWT_SECRET and JWT_SECRET != "your-secret-key-here" else "❌ Default утга",
            "chatwoot_base_url": CHATWOOT_BASE_URL,
            "docs_base_url": DOCS_BASE_URL,
            "vector_store_exists": os.path.exists(VECTOR_STORE_PATH)
        }
    }
    
    # Имэйл тохиргооны нарийвчилсан шалгалт
    email_issues = []
    if not SENDER_EMAIL:
        email_issues.append("SENDER_EMAIL тохируулаагүй")
    if not SENDER_PASSWORD:
        email_issues.append("SENDER_PASSWORD тохируулаагүй")
    if SMTP_SERVER == "smtp.gmail.com" and "@gmail.com" not in (SENDER_EMAIL or ""):
        email_issues.append("Gmail SMTP ашиглаж байгаа ч Gmail хаяг биш")
    
    if email_issues:
        status["email_issues"] = email_issues
    
    # Нийт статус шалгах
    all_ok = all(status["components"].values())
    if not all_ok:
        status["status"] = "warning"
        
    return jsonify(status), 200 if all_ok else 206

@app.route("/rebuild-docs", methods=["POST"])
def rebuild_docs():
    """Документын vector store дахин бүтээх"""
    try:
        logger.info("Документын vector store дахин бүтээж байна...")
        
        # Хуучин vector store устгах
        if os.path.exists(VECTOR_STORE_PATH):
            import shutil
            shutil.rmtree(VECTOR_STORE_PATH)
            logger.info("Хуучин vector store устгалаа")
        
        # Шинэ vector store үүсгэх
        global qa_chain, vectorstore
        
        # Документ цуглуулах
        docs = crawl_docs(DOCS_BASE_URL)
        if not docs:
            return jsonify({"error": "Документ олдсонгүй"}), 400
        
        # Vector store үүсгэх
        chunks = chunk_documents(docs)
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        vectorstore = FAISS.from_documents(chunks, embeddings)
        vectorstore.save_local(VECTOR_STORE_PATH)
        
        # QA chain дахин үүсгэх
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
        
        logger.info(f"Vector store амжилттай дахин бүтээлээ: {len(docs)} документ, {len(chunks)} chunk")
        
        return jsonify({
            "status": "success",
            "message": f"Документын vector store дахин бүтээлээ",
            "documents_count": len(docs),
            "chunks_count": len(chunks),
            "timestamp": datetime.utcnow().isoformat()
        }), 200
        
    except Exception as e:
        logger.error(f"Vector store дахин бүтээхэд алдаа: {str(e)}")
        return jsonify({"error": f"Алдаа: {str(e)}"}), 500

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