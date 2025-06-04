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

# RAG системийн импортууд
import json
import hashlib
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.docstore.document import Document
import pickle

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

# RAG систем тохиргоо
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").lower() == "true"
VECTOR_STORE_PATH = "cloudmn_vectorstore"
CRAWL_CACHE_FILE = "cloudmn_crawl_cache.json"
CRAWL_MAX_PAGES = int(os.environ.get("CRAWL_MAX_PAGES", "100"))
ESCALATION_THRESHOLD = int(os.environ.get("ESCALATION_THRESHOLD", "3"))  # Хэдэн асуулт гарсны дараа escalate хийх

# Global vector store
vector_store = None
embeddings = None

# Хэрэглэгчийн асуултын түүх хадгалах (conversation_id -> query_history)
user_query_history = {}
# URL хадгалах хэсэг (conversation_id -> last_urls)
user_last_urls = {}

# RAG системийг эхлүүлэх
if RAG_ENABLED:
    try:
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        if os.path.exists(f"{VECTOR_STORE_PATH}.faiss"):
            vector_store = FAISS.load_local(VECTOR_STORE_PATH, embeddings)
            print("✅ Хадгалагдсан vector store-г ачааллаа")
        else:
            print("⚠️ Vector store олдсонгүй - эхлээд crawl хийх хэрэгтэй")
    except Exception as e:
        print(f"❌ RAG систем эхлүүлэхэд алдаа: {e}")
        RAG_ENABLED = False

def is_valid_email(email):
    """Имэйл хаягийн форматыг шалгах"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def clean_text(text):
    """Текстийг цэвэрлэх"""
    # Олон мөр шилжих тэмдгийг нэг болгох
    text = re.sub(r'\n\s*\n', '\n', text)
    # Олон зайг нэг болгох
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_content_from_url(url, visited_urls=None):
    """Нэг URL-аас контент авах"""
    if visited_urls is None:
        visited_urls = set()
    
    if url in visited_urls:
        return None
    
    visited_urls.add(url)
    
    try:
        print(f"📄 Crawling: {url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Title авах
        title = soup.find('title')
        title_text = title.get_text().strip() if title else "Untitled"
        
        # Main content авах
        content_selectors = [
            'main', 'article', '.content', '#content',
            '.markdown', '.doc-content', '.documentation'
        ]
        
        content = None
        for selector in content_selectors:
            content = soup.select_one(selector)
            if content:
                break
        
        if not content:
            content = soup.find('body')
        
        if not content:
            return None
        
        # Script, style гэх мэт элементүүдийг устгах
        for element in content(["script", "style", "nav", "header", "footer", "aside"]):
            element.decompose()
        
        # Текст контент авах
        text_content = content.get_text()
        text_content = clean_text(text_content)
        
        if len(text_content.strip()) < 50:  # Хэт богино контент алгасах
            return None
        
        return {
            'url': url,
            'title': title_text,
            'content': text_content,
            'length': len(text_content)
        }
        
    except Exception as e:
        print(f"❌ Error crawling {url}: {e}")
        return None

def crawl_cloudmn_docs():
    """CloudMN docs сайтыг crawl хийх"""
    base_url = "https://docs.cloud.mn"
    start_urls = [
        "https://docs.cloud.mn/",
    ]
    
    visited_urls = set()
    all_documents = []
    
    # Cache файлыг шалгах
    if os.path.exists(CRAWL_CACHE_FILE):
        try:
            with open(CRAWL_CACHE_FILE, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
                print(f"✅ Cache-аас {len(cached_data)} хуудас ачааллаа")
                return cached_data
        except Exception as e:
            print(f"⚠️ Cache уншихад алдаа: {e}")
    
    print(f"🕷️ CloudMN docs crawl эхэлж байна... (Max: {CRAWL_MAX_PAGES} хуудас)")
    
    urls_to_visit = start_urls.copy()
    page_count = 0
    
    while urls_to_visit and page_count < CRAWL_MAX_PAGES:
        current_url = urls_to_visit.pop(0)
        
        if current_url in visited_urls:
            continue
        
        page_data = extract_content_from_url(current_url, visited_urls)
        if page_data:
            all_documents.append(page_data)
            page_count += 1
            print(f"✅ [{page_count}/{CRAWL_MAX_PAGES}] {page_data['title']}")
            
            # Шинэ линкүүд олох
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                response = requests.get(current_url, headers=headers, timeout=10)
                soup = BeautifulSoup(response.content, 'html.parser')
                
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    full_url = urljoin(current_url, href)
                    
                    # CloudMN docs доторх линк мөн эсэхийг шалгах
                    if (full_url.startswith(base_url) and 
                        full_url not in visited_urls and 
                        full_url not in urls_to_visit and
                        not full_url.endswith(('.pdf', '.jpg', '.png', '.gif', '.css', '.js'))):
                        urls_to_visit.append(full_url)
                        
            except Exception as e:
                print(f"⚠️ Линк олохоор алдаа {current_url}: {e}")
        
        time.sleep(0.5)  # Сайтыг хэт дарамтлахгүйн тулд
    
    print(f"✅ Crawl дууслаа: {len(all_documents)} хуудас")
    
    # Cache хадгалах
    try:
        with open(CRAWL_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_documents, f, ensure_ascii=False, indent=2)
        print(f"💾 Cache хадгаллаа: {CRAWL_CACHE_FILE}")
    except Exception as e:
        print(f"⚠️ Cache хадгалахад алдаа: {e}")
    
    return all_documents

def build_vector_store():
    """Vector store үүсгэх"""
    global vector_store, embeddings
    
    if not RAG_ENABLED:
        print("❌ RAG идэвхгүй байна")
        return False
    
    try:
        print("🔧 Vector store үүсгэж байна...")
        
        # Документууд crawl хийх
        documents_data = crawl_cloudmn_docs()
        
        if not documents_data:
            print("❌ Crawl хийх документ олдсонгүй")
            return False
        
        # Text splitter
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", "! ", "? ", " "]
        )
        
        # Документуудыг chunks болгох
        all_chunks = []
        for doc_data in documents_data:
            # Текстийг хэсэглэх
            chunks = text_splitter.split_text(doc_data['content'])
            
            for i, chunk in enumerate(chunks):
                if len(chunk.strip()) > 50:  # Хэт богино chunk алгасах
                    doc = Document(
                        page_content=chunk,
                        metadata={
                            'title': doc_data['title'],
                            'url': doc_data['url'],
                            'chunk_id': i,
                            'total_chunks': len(chunks)
                        }
                    )
                    all_chunks.append(doc)
        
        print(f"📄 {len(all_chunks)} ширхэг chunk үүсгэлээ")
        
        if not all_chunks:
            print("❌ Боловсруулах chunk олдсонгүй")
            return False
        
        # Vector store үүсгэх
        if not embeddings:
            embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        
        print("🔮 Embeddings үүсгэж байна...")
        vector_store = FAISS.from_documents(all_chunks, embeddings)
        
        # Хадгалах
        vector_store.save_local(VECTOR_STORE_PATH)
        print(f"💾 Vector store хадгаллаа: {VECTOR_STORE_PATH}")
        
        return True
        
    except Exception as e:
        print(f"❌ Vector store үүсгэхэд алдаа: {e}")
        return False

def search_cloudmn_docs(query, k=5):
    """CloudMN docs-аас хайлт хийх"""
    global vector_store
    
    if not RAG_ENABLED or not vector_store:
        return []
    
    try:
        # Similarity search
        results = vector_store.similarity_search(query, k=k)
        
        search_results = []
        for doc in results:
            search_results.append({
                'content': doc.page_content,
                'title': doc.metadata.get('title', 'Unknown'),
                'url': doc.metadata.get('url', ''),
                'chunk_id': doc.metadata.get('chunk_id', 0)
            })
        
        return search_results
        
    except Exception as e:
        print(f"❌ RAG хайлт алдаа: {e}")
        return []

def is_similar_query(query1, query2, threshold=0.7):
    """Хоёр асуултын ижил төрөл эсэхийг GPT-ээр шалгах"""
    try:
        system_msg = """Та бол асуултын ижил төрлийг тодорхойлох мэргэжилтэн. 
        Хоёр асуулт ижил төрлийн асуудлын талаар байгаа эсэхийг тодорхойлно уу.
        Зөвхөн 'ИЖИЛ' эсвэл 'ӨӨРЛӨГ' гэж хариулна уу."""
        
        user_msg = f"""
        Асуулт 1: "{query1}"
        Асуулт 2: "{query2}"
        
        Эдгээр хоёр асуулт ижил төрлийн асуудлын талаар байгаа юу?
        - Хэрэв ижил төрлийн техникийн асуудал бол: ИЖИЛ
        - Хэрэв өөр төрлийн асуудал бол: ӨӨРЛӨГ
        """
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=10,
            temperature=0.1
        )
        
        result = response.choices[0].message.content.strip()
        return "ИЖИЛ" in result
        
    except Exception as e:
        print(f"❌ Асуулт харьцуулах алдаа: {e}")
        return False

def should_search_new_content(conv_id, current_query):
    """Шинэ контент хайх эсэхийг шийдэх"""
    global user_query_history
    
    # Хэрэв энэ conversation-д өмнө асуулт байгаагүй бол шинэ хайлт хийх
    if conv_id not in user_query_history:
        user_query_history[conv_id] = [current_query]
        return True, "Анхны асуулт"
    
    # Сүүлийн асуулттай харьцуулах
    previous_queries = user_query_history[conv_id]
    last_query = previous_queries[-1] if previous_queries else ""
    
    # Хэрэв ижил төрлийн асуулт бол шинэ хайлт хийхгүй
    if last_query and is_similar_query(current_query, last_query):
        return False, "Ижил төрлийн асуулт"
    
    # Шинэ төрлийн асуулт бол хайлт хийх
    user_query_history[conv_id].append(current_query)
    
    # Түүхийг хязгаарлах (сүүлийн 10 асуулт)
    if len(user_query_history[conv_id]) > 10:
        user_query_history[conv_id] = user_query_history[conv_id][-10:]
    
    return True, "Шинэ төрлийн асуулт"

def should_escalate_to_support(conv_id, current_message):
    """Дэмжлэгийн багт илгээх эсэхийг шийдэх (хязгаарлагдсан логик)"""
    global user_query_history
    
    try:
        # Хэрэв RAG идэвхгүй бол escalate хийх
        if not RAG_ENABLED:
            return True, "RAG систем идэвхгүй"
        
        # Асуултын тоог шалгах
        query_count = len(user_query_history.get(conv_id, []))
        
        # Хэрэв тодорхой тооноос илүү асуулт гарсан бол escalate хийх
        if query_count >= ESCALATION_THRESHOLD:
            return True, f"Олон асуулт гарсан ({query_count} >= {ESCALATION_THRESHOLD})"
        
        # Тусгай түлхүүр үгс байгаа эсэхийг шалгах
        urgent_keywords = [
            "алдаа гарч байна", "ажиллахгүй байна", "буруу", "асуудал",
            "тусламж хэрэгтэй", "яаралтай", "хариу ирэхгүй", "холбогдохгүй"
        ]
        
        message_lower = current_message.lower()
        if any(keyword in message_lower for keyword in urgent_keywords):
            return True, "Яаралтай түлхүүр үг олдсон"
        
        # Бусад тохиолдолд escalate хийхгүй
        return False, "Хэвийн асуулт"
        
    except Exception as e:
        print(f"❌ Escalation шийдэх алдаа: {e}")
        return False, "Алдаа гарсан"

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
        print(f"🔍 verify_token: Starting verification for token: {token[:50]}...")
        print(f"🔑 JWT_SECRET: {'SET' if JWT_SECRET and JWT_SECRET != 'your-secret-key-here' else 'DEFAULT/NOT SET'}")
        
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        print(f"✅ JWT decode амжилттай: {payload}")
        return payload
        
    except jwt.ExpiredSignatureError as e:
        print(f"⏰ JWT хугацаа дууссан: {e}")
        return None
    except jwt.InvalidTokenError as e:
        print(f"❌ JWT токен буруу: {e}")
        return None
    except Exception as e:
        print(f"💥 verify_token алдаа: {e}")
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
    try:
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
        payload = {"custom_attributes": attrs}
        headers = {"api_access_token": CHATWOOT_API_KEY}
        
        print(f"🔗 Chatwoot API URL: {url}")
        print(f"🔑 Using API Key: {CHATWOOT_API_KEY[:10]}..." if CHATWOOT_API_KEY else "❌ API Key бүр байхгүй")
        print(f"📊 Payload: {payload}")
        
        resp = requests.put(url, json=payload, headers=headers)
        
        print(f"📈 Response status: {resp.status_code}")
        print(f"📄 Response text: {resp.text[:200]}...")
        
        resp.raise_for_status()
        return resp.json()
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ Chatwoot API HTTP алдаа: {e}")
        print(f"📊 Response status: {resp.status_code}")
        print(f"📄 Response text: {resp.text}")
        raise e
    except Exception as e:
        print(f"💥 Contact update алдаа: {e}")
        raise e

def get_conversation(conv_id):
    """Conversation мэдээлэл авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()

def update_conversation(conv_id, attrs):
    """Conversation-ийн custom attributes шинэчлэх"""
    try:
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes"
        payload = {"custom_attributes": attrs}
        headers = {"api_access_token": CHATWOOT_API_KEY}
        
        print(f"🔗 Conversation API URL: {url}")
        print(f"📊 Payload: {payload}")
        
        resp = requests.post(url, json=payload, headers=headers)
        
        print(f"📈 Response status: {resp.status_code}")
        print(f"📄 Response text: {resp.text[:200]}...")
        
        resp.raise_for_status()
        return resp.json()
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ Conversation API HTTP алдаа: {e}")
        print(f"📊 Response status: {resp.status_code}")
        print(f"📄 Response text: {resp.text}")
        raise e
    except Exception as e:
        print(f"💥 Conversation update алдаа: {e}")
        raise e

def send_to_chatwoot(conv_id, text):
    """Chatwoot руу мессеж илгээх"""
    try:
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
        headers = {"api_access_token": CHATWOOT_API_KEY}
        payload = {"content": text, "message_type": "outgoing"}
        
        print(f"🔗 Message API URL: {url}")
        print(f"📊 Message payload: {payload}")
        
        r = requests.post(url, json=payload, headers=headers)
        
        print(f"📈 Message response status: {r.status_code}")
        print(f"📄 Message response text: {r.text[:200]}...")
        
        r.raise_for_status()
        return r.json()
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ Message API HTTP алдаа: {e}")
        print(f"📊 Response status: {r.status_code}")
        print(f"📄 Response text: {r.text}")
        raise e
    except Exception as e:
        print(f"💥 Message send алдаа: {e}")
        raise e

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

def get_ai_response_with_rag(thread_id, message_content, conv_id=None, customer_email=None, retry_count=0):
    """RAG системтэй AI хариулт авах"""
    global user_last_urls
    
    try:
        # CloudMN docs-аас холбогдох мэдээлэл хайх
        rag_context = ""
        used_urls = []
        
        if RAG_ENABLED and vector_store and conv_id:
            # Шинэ контент хайх эсэхийг шийдэх
            should_search, search_reason = should_search_new_content(conv_id, message_content)
            print(f"🔍 Хайлт шийдвэр: {should_search} - {search_reason}")
            
            if should_search:
                # Шинэ хайлт хийх
                search_results = search_cloudmn_docs(message_content, k=3)
                if search_results:
                    print(f"📚 {len(search_results)} үр дүн олдлоо")
                    rag_context = "\n\nCloudMN баримт бичгээс олсон холбогдох мэдээлэл:\n"
                    
                    # URL цуглуулах
                    new_urls = []
                    for i, result in enumerate(search_results, 1):
                        rag_context += f"\n{i}. {result['title']} - {result['url']}\n{result['content'][:500]}...\n"
                        if result['url'] and result['url'] not in new_urls:
                            new_urls.append(result['url'])
                    
                    # URL хадгалах
                    user_last_urls[conv_id] = new_urls
                    used_urls = new_urls
                    print(f"🔗 Шинэ URL хадгаллаа: {len(new_urls)} ширхэг")
                else:
                    print("❌ Хайлтын үр дүн олдсонгүй")
            else:
                # Өмнөх URL ашиглах
                if conv_id in user_last_urls and user_last_urls[conv_id]:
                    used_urls = user_last_urls[conv_id]
                    print(f"🔗 Өмнөх URL ашиглаж байна: {len(used_urls)} ширхэг")
                    
                    # Өмнөх мэдээллийг дурдах
                    rag_context = f"\n\nТа өмнө дараах CloudMN хуудсуудыг үзэж болно:\n"
                    for i, url in enumerate(used_urls[:3], 1):
                        rag_context += f"{i}. {url}\n"
                else:
                    print("⚠️ Өмнөх URL олдсонгүй")
        
        # Assistant-д мэдээлэл дамжуулах
        enhanced_message = message_content
        if rag_context:
            enhanced_message += rag_context
        
        # Хэрэглэгчийн мессежийг thread руу нэмэх
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=enhanced_message
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
                        
                # URL-ыг хариултын төгсгөлд нэмэх
                if used_urls and conv_id:
                    reply += f"\n\n📋 Дэлгэрэнгүй мэдээлэл авах бол дараах хуудсуудыг үзэж болно:\n"
                    for url in used_urls[:3]:  # Эхний 3-ыг харуулах
                        reply += f"• {url}\n"
                        
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
    print("📩 /verify дуудлаа")

    token = request.args.get('token')
    if not token:
        return "❌ Токен байхгүй байна!", 400

    payload = verify_token(token)
    if not payload:
        return "❌ Токен хүчингүй эсвэл хугацаа дууссан байна!", 400

    try:
        contact_id = payload['contact_id']
        email = payload['email']
        conv_id = payload.get('conv_id', None)

        # ✅ Зөвхөн Contact-г verified тэмдэглэх
        update_result = update_contact(contact_id, {
            "email_verified": "1",
            "verified_email": email,
            "verification_date": datetime.utcnow().isoformat()
        })
        print(f"✅ Contact update: {update_result}")

        # ✅ Хэрэв conv_id байгаа бол тэр conversation дээр амжилтын мэдээлэл илгээх
        if conv_id:
            send_to_chatwoot(conv_id, f"✅ Таны имэйл хаяг ({email}) амжилттай баталгаажлаа! Одоо chatbot-той харилцах боломжтой боллоо.")

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
            <div class="info">Таны имэйл хаяг ({{ email }}) баталгаажсан байна.<br>Одоо та chatbot-той харилцах боломжтой боллоо.</div>
        </body>
        </html>
        """, email=email)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ Баталгаажуулахад алдаа гарлаа: {str(e)}", 500

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
            ai_response = get_ai_response_with_rag(thread_id, message_content, conv_id, verified_email, retry_count)
            
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
            should_escalate, reason = should_escalate_to_support(conv_id, message_content)
            
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
        test_analysis = """АСУУДЛЫН ТӨРӨЛ : Teams интеграцийн тест
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
            "Хэрэглэгчийн асуудлын дүгнэлт:",
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

# def should_escalate_to_teams(thread_id, current_message):
#     """Тухайн асуудлыг Teams-д илгээх хэрэгтэй эсэхийг шийдэх (хуучин функц - ашиглагддаггүй)"""
#     # Энэ функц ашиглагддаггүй - should_escalate_to_support ашиглах
#     return False, "Хуучин функц - ашиглагддаггүй"

@app.route("/debug-env", methods=["GET"])
def debug_env():
    """Орчны хувьсагчдыг шалгах debug endpoint"""
    global user_query_history, user_last_urls
    
    return {
        "JWT_SECRET": "SET" if JWT_SECRET else "NOT SET",
        "OPENAI_API_KEY": "SET" if OPENAI_API_KEY else "NOT SET", 
        "ASSISTANT_ID": "SET" if ASSISTANT_ID else "NOT SET",
        "CHATWOOT_API_KEY": "SET" if CHATWOOT_API_KEY else "NOT SET",
        "ACCOUNT_ID": "SET" if ACCOUNT_ID else "NOT SET",
        "SMTP_SERVER": SMTP_SERVER,
        "SMTP_PORT": SMTP_PORT,
        "SENDER_EMAIL": "SET" if SENDER_EMAIL else "NOT SET",
        "SENDER_PASSWORD": "SET" if SENDER_PASSWORD else "NOT SET",
        "TEAMS_WEBHOOK_URL": "SET" if TEAMS_WEBHOOK_URL else "NOT SET",
        "VERIFICATION_URL_BASE": VERIFICATION_URL_BASE,
        "RAG_ENABLED": RAG_ENABLED,
        "ESCALATION_THRESHOLD": ESCALATION_THRESHOLD,
        "active_conversations": len(user_query_history),
        "conversations_with_urls": len(user_last_urls)
    }

@app.route("/rag/build", methods=["POST"])
def build_rag():
    """RAG vector store үүсгэх/шинэчлэх"""
    if not RAG_ENABLED:
        return jsonify({"error": "RAG систем идэвхгүй байна"}), 400
    
    try:
        print("🚀 RAG систем үүсгэх хүсэлт ирлээ")
        success = build_vector_store()
        
        if success:
            return jsonify({
                "status": "success", 
                "message": "Vector store амжилттай үүсгэлээ!",
                "vector_store_path": VECTOR_STORE_PATH
            }), 200
        else:
            return jsonify({"error": "Vector store үүсгэхэд алдаа гарлаа"}), 500
            
    except Exception as e:
        return jsonify({"error": f"Алдаа: {str(e)}"}), 500

@app.route("/rag/search", methods=["POST"])
def search_rag():
    """RAG системээр хайлт хийх (тест зорилгоор)"""
    if not RAG_ENABLED:
        return jsonify({"error": "RAG систем идэвхгүй байна"}), 400
    
    if not vector_store:
        return jsonify({"error": "Vector store үүсгэгдээгүй байна. /rag/build дуудна уу."}), 400
    
    try:
        data = request.json
        query = data.get("query", "").strip()
        k = data.get("k", 5)
        
        if not query:
            return jsonify({"error": "Query заавал байх ёстой"}), 400
        
        results = search_cloudmn_docs(query, k=k)
        
        return jsonify({
            "status": "success",
            "query": query,
            "results_count": len(results),
            "results": results
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"Хайлт алдаа: {str(e)}"}), 500

@app.route("/rag/status", methods=["GET"])
def rag_status():
    """RAG системийн статус шалгах"""
    global vector_store
    
    status = {
        "rag_enabled": RAG_ENABLED,
        "vector_store_exists": vector_store is not None,
        "vector_store_path": VECTOR_STORE_PATH,
        "cache_file": CRAWL_CACHE_FILE,
        "cache_exists": os.path.exists(CRAWL_CACHE_FILE),
        "max_crawl_pages": CRAWL_MAX_PAGES
    }
    
    # Cache file мэдээлэл
    if status["cache_exists"]:
        try:
            with open(CRAWL_CACHE_FILE, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
                status["cached_pages"] = len(cached_data)
        except:
            status["cached_pages"] = "Unknown"
    
    # Vector store файлууд шалгах
    faiss_file = f"{VECTOR_STORE_PATH}.faiss"
    pkl_file = f"{VECTOR_STORE_PATH}.pkl"
    status["vector_files"] = {
        "faiss_exists": os.path.exists(faiss_file),
        "pkl_exists": os.path.exists(pkl_file)
    }
    
    return jsonify(status), 200

@app.route("/rag/refresh", methods=["POST"])
def refresh_rag():
    """Cache цэвэрлэж, шинээр crawl хийж vector store үүсгэх"""
    if not RAG_ENABLED:
        return jsonify({"error": "RAG систем идэвхгүй байна"}), 400
    
    try:
        # Cache файл устгах
        if os.path.exists(CRAWL_CACHE_FILE):
            os.remove(CRAWL_CACHE_FILE)
            print(f"🗑️ Cache файл устгалаа: {CRAWL_CACHE_FILE}")
        
        # Vector store файлууд устгах
        for ext in ['.faiss', '.pkl']:
            file_path = f"{VECTOR_STORE_PATH}{ext}"
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🗑️ Vector store файл устгалаа: {file_path}")
        
        # Дахин үүсгэх
        success = build_vector_store()
        
        if success:
            return jsonify({
                "status": "success", 
                "message": "RAG систем шинээр үүсгэлээ (Cache цэвэрлэсэн)",
                "vector_store_path": VECTOR_STORE_PATH
            }), 200
        else:
            return jsonify({"error": "Шинээр үүсгэхэд алдаа гарлаа"}), 500
            
    except Exception as e:
        return jsonify({"error": f"Refresh алдаа: {str(e)}"}), 500

@app.route("/rag/test-query", methods=["POST"])
def test_query_logic():
    """RAG системийн асуултын логикийг тест хийх"""
    global user_query_history, user_last_urls
    
    if not RAG_ENABLED:
        return jsonify({"error": "RAG систем идэвхгүй байна"}), 400
    
    try:
        data = request.json
        conv_id = data.get("conv_id", "test_conversation")
        query = data.get("query", "").strip()
        
        if not query:
            return jsonify({"error": "Query заавал байх ёстой"}), 400
        
        # Асуултын түүхийг шалгах
        should_search, reason = should_search_new_content(conv_id, query)
        
        # Escalation шалгах
        should_escalate, escalate_reason = should_escalate_to_support(conv_id, query)
        
        # Одоогийн статус
        current_queries = user_query_history.get(conv_id, [])
        current_urls = user_last_urls.get(conv_id, [])
        
        response = {
            "status": "success",
            "conv_id": conv_id,
            "query": query,
            "should_search_new": should_search,
            "search_reason": reason,
            "should_escalate": should_escalate,
            "escalate_reason": escalate_reason,
            "query_history": current_queries,
            "saved_urls": current_urls,
            "total_queries": len(current_queries)
        }
        
        # Хэрэв шинэ хайлт хийх бол тест хайлт хийх
        if should_search and vector_store:
            search_results = search_cloudmn_docs(query, k=2)
            response["test_search_results"] = len(search_results)
            response["test_urls"] = [r['url'] for r in search_results if r['url']]
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": f"Тест алдаа: {str(e)}"}), 500

@app.route("/rag/clear-history", methods=["POST"])
def clear_history():
    """Асуултын түүх болон URL-ыг цэвэрлэх"""
    global user_query_history, user_last_urls
    
    try:
        data = request.json
        conv_id = data.get("conv_id", "all")
        
        if conv_id == "all":
            # Бүгдийг цэвэрлэх
            cleared_conversations = len(user_query_history)
            cleared_urls = len(user_last_urls)
            user_query_history.clear()
            user_last_urls.clear()
            
            return jsonify({
                "status": "success",
                "message": "Бүх conversation түүх цэвэрлэгдлээ",
                "cleared_conversations": cleared_conversations,
                "cleared_url_maps": cleared_urls
            }), 200
        else:
            # Тодорхой conversation цэвэрлэх
            queries_removed = len(user_query_history.get(conv_id, []))
            urls_removed = len(user_last_urls.get(conv_id, []))
            
            if conv_id in user_query_history:
                del user_query_history[conv_id]
            if conv_id in user_last_urls:
                del user_last_urls[conv_id]
            
            return jsonify({
                "status": "success",
                "message": f"Conversation {conv_id} түүх цэвэрлэгдлээ",
                "queries_removed": queries_removed,
                "urls_removed": urls_removed
            }), 200
            
    except Exception as e:
        return jsonify({"error": f"Цэвэрлэх алдаа: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)