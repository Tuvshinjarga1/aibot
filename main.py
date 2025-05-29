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
        chunk_size=500,  # Reduced chunk size to fit token limit
        chunk_overlap=50,  # Reduced overlap
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
                "answer": None,
                "sources": [],
                "confidence": 0
            }
        
        # Get answer with source documents
        result = qa_chain.invoke({"query": question})
        answer = result["result"]
        sources = result.get("source_documents", [])
        
        # Хариултын чанарыг үнэлэх
        confidence_score = evaluate_answer_quality(answer, question, sources)
        
        # Хэрэв хариулт хангалтгүй бол None буцаах
        if confidence_score < 0.25:  # 25%-аас бага итгэлтэй бол
            return {
                "answer": None,
                "sources": [],
                "confidence": confidence_score
            }
        
        # Format response with sources
        response = {
            "answer": answer,
            "sources": [],
            "confidence": confidence_score
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
            "answer": None,
            "sources": [],
            "confidence": 0
        }

def evaluate_answer_quality(answer: str, question: str, sources: list) -> float:
    """Хариултын чанарыг үнэлэх (0.0 - 1.0)"""
    try:
        if not answer or len(answer.strip()) < 10:
            return 0.0
        
        score = 0.0
        
        # Sources байгаа эсэхийг шалгах (40% оноо)
        if sources and len(sources) > 0:
            score += 0.4
            # Олон source байвал илүү сайн
            if len(sources) >= 2:
                score += 0.1
        
        # Хариултын урт шалгах (30% оноо)
        answer_length = len(answer.strip())
        if answer_length >= 100:
            score += 0.3
        elif answer_length >= 50:
            score += 0.2
        elif answer_length >= 20:
            score += 0.1
        
        # Асуулттай холбоотой эсэхийг энгийнээр шалгах (30% оноо)
        question_words = set(question.lower().split())
        answer_words = set(answer.lower().split())
        common_words = question_words.intersection(answer_words)
        
        if len(common_words) >= 3:
            score += 0.3
        elif len(common_words) >= 2:
            score += 0.2
        elif len(common_words) >= 1:
            score += 0.1
        
        # Максимум 1.0 болгох
        return min(score, 1.0)
        
    except Exception as e:
        logger.error(f"Хариулт үнэлэхэд алдаа: {str(e)}")
        return 0.5

# Initialize RAG system
try:
    vectorstore = load_vectorstore()
    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 3}
    )
    
    qa_chain = RetrievalQA.from_chain_type(
        llm=LC_OpenAI(
            openai_api_key=OPENAI_API_KEY, 
            temperature=0.1,
            max_tokens=500,
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

def analyze_undocumented_issue(thread_id, current_message, customer_email=None, rag_result=None):
    """Документээс олдохгүй асуудлыг AI-аар дүгнэж дэмжлэгийн багт илгээх"""
    try:
        # OpenAI thread-с сүүлийн 10 мессежийг авах
        messages = client.beta.threads.messages.list(thread_id=thread_id, limit=10)
        
        # Хэрэглэгчийн мессежүүдийг цуглуулах
        conversation_history = []
        for msg in reversed(messages.data):
            if msg.role == "user":
                content = ""
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        content += content_block.text.value
                if content.strip():
                    conversation_history.append(f"Хэрэглэгч: {content.strip()}")
        
        # Хэрэв чат түүх хоосон бол одоогийн мессежээр дүгнэх
        if not conversation_history:
            conversation_history = [f"Хэрэглэгч: {current_message}"]
        
        # Conversation түүхийг string болгох
        chat_history = "\n".join(conversation_history[-5:])
        
        # RAG хайлтын үр дүнгийн мэдээлэл
        rag_info = ""
        if rag_result:
            confidence = rag_result.get("confidence", 0)
            sources_count = len(rag_result.get("sources", []))
            rag_info = f"RAG хайлт: {sources_count} эх сурвалж олдсон, итгэлтэй байдал: {confidence:.2f}"
        
        # Дэлгэрэнгүй system prompt
        system_msg = (
            "Та бол дэмжлэгийн мэргэжилтэн. "
            "Хэрэглэгчийн асуудлыг документаас олдохгүй тул дэлгэрэнгүй дүгнэж, "
            "дэмжлэгийн багт тодорхой зөвлөмж өгнө үү."
        )

        user_msg = f'''Хэрэглэгчийн чат түүх:
{chat_history}

Одоогийн асуулт: "{current_message}"

{rag_info}

Дараах форматаар дэлгэрэнгүй дүгнэлт өгнө үү:

АСУУДЛЫН ТӨРӨЛ: [Техникийн/Худалдааны/Мэдээллийн/Гомдол/Шинэ хүсэлт]
ЯАРАЛТАЙ БАЙДАЛ: [Өндөр/Дунд/Бага]
ДОКУМЕНТ ХАМРАХ ХЭРЭГТЭЙ: [Тийм/Үгүй]
АСУУДЛЫН ДЭЛГЭРЭНГҮЙ ТАЙЛБАР: [2-3 өгүүлбэрээр]
БОЛОМЖИТ ШИЙДЭЛ: [Дэмжлэгийн багийн хийх ёстой арга хэмжээ]
ХҮЛЭЭГДЭЖ БУЙ ХАРИУЛТ: [Хэрэглэгчид өгөх ёстой хариултын төрөл]
АНХААРАХ ЗҮЙЛ: [Тусгай анхаарах шаардлагатай зүйлс]'''

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=400,
            temperature=0.2,
            timeout=20
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"❌ Документээс олдохгүй асуудал дүгнэхэд алдаа: {e}")
        # Fallback дүгнэлт
        return f"""АСУУДЛЫН ТӨРӨЛ: Тодорхойгүй
ЯАРАЛТАЙ БАЙДАЛ: Дунд
ДОКУМЕНТ ХАМРАХ ХЭРЭГТЭЙ: Тийм
АСУУДЛЫН ДЭЛГЭРЭНГҮЙ ТАЙЛБАР: {current_message[:200]}
БОЛОМЖИТ ШИЙДЭЛ: Дэмжлэгийн мэргэжилтний анхаарал шаардлагатай
ХҮЛЭЭГДЭЖ БУЙ ХАРИУЛТ: Асуудлын тодорхой хариулт
АНХААРАХ ЗҮЙЛ: Документээс олдохгүй шинэ асуудал"""

def send_teams_notification(conv_id, customer_message, customer_email=None, escalation_reason="Хэрэглэгчийн асуудал", ai_analysis=None):
    """Microsoft Teams руу документээс олдохгүй асуудлын талаар дэмжлэгийн багт мэдээлэх"""
    if not TEAMS_WEBHOOK_URL:
        print("⚠️ Teams webhook URL тохируулаагүй байна")
        return False
    
    try:
        # Chatwoot conversation URL
        conv_url = f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
        
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
                            "text": "📋 Документээс олдохгүй асуудал",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": "Attention"
                        },
                        {
                            "type": "TextBlock",
                            "text": "RAG систем документаас хариулт олж чадаагүй. AI дүгнэлт хийж, дэмжлэгийн багт дамжуулж байна.",
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
                                    "title": "Хэрэглэгчийн асуулт:",
                                    "value": customer_message[:300] + ("..." if len(customer_message) > 300 else "")
                                },
                                {
                                    "title": "Хугацаа:",
                                    "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                },
                                {
                                    "title": "Шалтгаан:",
                                    "value": escalation_reason
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
        print(f"✅ Teams мэдээлэл илгээлээ: {escalation_reason}")
        return True
        
    except Exception as e:
        print(f"❌ Teams мэдээлэл илгээхэд алдаа: {e}")
        return False

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
        
        # ========== RAG СИСТЕМЭЭР ДОКУМЕНТ ХАЙЛТ ==========
        print("📚 Бүх асуултанд RAG системээр эхлээд хайж байна...")
        
        ai_response = None
        used_rag = False
        rag_attempted = True
        
        # RAG-аар хариулт хайх
        rag_result = search_docs_with_rag(message_content)
        
        # RAG хариултын чанарыг шалгах
        if rag_result["answer"] and rag_result.get("confidence", 0) >= 0.25:  # 25%-аас дээш итгэлтэй бол
            # RAG хариултыг форматлах
            ai_response = rag_result["answer"]
            
            # Source links нэмэх
            if rag_result["sources"]:
                ai_response += "\n\n📚 **Холбогдох документууд:**\n"
                for i, source in enumerate(rag_result["sources"], 1):
                    title = source.get("title", "Документ")
                    url = source.get("url", "")
                    ai_response += f"{i}. [{title}]({url})\n"
            
            used_rag = True
            print(f"✅ RAG хариулт олдлоо (итгэлтэй: {rag_result.get('confidence', 0):.2f}): {ai_response[:100]}...")
        else:
            confidence = rag_result.get("confidence", 0)
            print(f"❌ RAG хариулт хангалтгүй (итгэлтэй: {confidence:.2f}) - AI Assistant-д шилжүүлж байна")
        
        # ========== STANDARD AI ASSISTANT (хэрэв RAG ашиглаагүй бол) ==========
        if not used_rag:
            print("🤖 RAG хариулт олдсонгүй - AI Assistant-аар дүгнэж дэмжлэгийн багт мэдэгдэх...")
            
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

            # AI-аар асуудлыг дүгнэх (документээс олдохгүй асуудал)
            print("🔍 AI-аар асуудлыг дүгнэж байна...")
            try:
                analysis = analyze_undocumented_issue(thread_id, message_content, verified_email, rag_result)
                print(f"✅ Асуудлын дүгнэлт бэлэн: {analysis[:100]}...")
                
                # Дэмжлэгийн багт мэдэгдэх
                escalation_success = send_teams_notification(
                    conv_id,
                    message_content,
                    verified_email,
                    "Документээс олдохгүй асуудал - AI дүгнэлт",
                    analysis
                )
                
                if escalation_success:
                    ai_response = (
                        "🔍 Таны асуултыг документаас хайсан боловч тохирох хариулт олдсонгүй.\n\n"
                        "🤖 AI системээр дүгнэж, дэмжлэгийн багт дамжуулсан.\n\n"
                        "👥 Мэргэжилтэн удахгүй тантай холбогдож, асуудлыг шийдэх болно.\n\n"
                        "🕐 Түр хүлээнэ үү..."
                    )
                else:
                    ai_response = (
                        "🔍 Таны асуултыг документаас хайсан боловч тохирох хариулт олдсонгүй.\n\n"
                        "⚠️ Дэмжлэгийн багт мэдэгдэхэд алдаа гарлаа.\n\n"
                        "📞 Шууд холбогдоно уу эсвэл дахин оролдоно уу."
                    )
                
                print("✅ Документээс олдохгүй асуудлыг дэмжлэгийн багт мэдэгдлээ")
                
            except Exception as e:
                print(f"❌ Асуудал дүгнэхэд алдаа: {e}")
                ai_response = (
                    "🔍 Таны асуултыг документаас хайсан боловч тохирох хариулт олдсонгүй.\n\n"
                    "⚠️ Системийн алдаа гарлаа.\n\n"
                    "📞 Шууд дэмжлэгийн багтай холбогдоно уу."
                )
        
        # ========== ХАРИУЛТ ИЛГЭЭХ ==========
        # Chatwoot руу илгээх
        response_type = "RAG" if used_rag else "AI дүгнэлт + Дэмжлэгийн баг"
        send_to_chatwoot(conv_id, ai_response)
        print(f"✅ {response_type} хариулт илгээлээ: {ai_response[:50]}...")
        
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
            "confidence": result.get("confidence", 0),
            "has_answer": result["answer"] is not None and result.get("confidence", 0) >= 0.25,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        logger.info(f"RAG хариулт: {len(result['sources'])} sources олдлоо, итгэлтэй: {result.get('confidence', 0):.2f}")
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
        }
    }
    
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
            search_kwargs={"k": 3}
        )
        
        qa_chain = RetrievalQA.from_chain_type(
            llm=LC_OpenAI(
                openai_api_key=OPENAI_API_KEY, 
                temperature=0.1,
                max_tokens=500,
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

if __name__ == "__main__":
    app.run(debug=True, port=5000)