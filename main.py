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
# CloudMN documentation crawler болон vector search-ийн dependencies
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json
from typing import Dict, List, Set, Tuple, Optional
import numpy as np
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.schema import Document
import tiktoken

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

# CloudMN documentation тохиргоо
CLOUDMN_DOCS_BASE = "https://docs.cloud.mn/"
CRAWL_DELAY = 1  # Секундээр хэлбэрээр серверт ачаалал багасгах
MAX_CRAWL_PAGES = 50  # Максимум хэдэн хуудас авах
VECTOR_SIMILARITY_THRESHOLD = 0.75  # Vector similarity хязгаар (0-1)
MAX_VECTOR_RESULTS = 3  # Хамгийн их хэдэн үр дүн буцаах

# OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY)

# CloudMN документацийн кэш хадгалах
cloudmn_docs_cache = {}
last_crawl_time = None
vector_store = None
last_vector_store_update = None

class CloudMNDocsCrawler:
    """CloudMN documentation сайтыг crawl хийх класс"""
    
    def __init__(self, base_url: str = CLOUDMN_DOCS_BASE):
        self.base_url = base_url
        self.visited_urls: Set[str] = set()
        self.docs_content: Dict[str, Dict] = {}
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'CloudMN-ChatBot-Crawler/1.0 (Educational Purpose)'
        })
    
    def is_valid_docs_url(self, url: str) -> bool:
        """URL нь CloudMN docs сайтын хэсэг мөн эсэхийг шалгах"""
        parsed = urlparse(url)
        return parsed.netloc == 'docs.cloud.mn' and not url.endswith(('.pdf', '.jpg', '.png', '.gif'))
    
    def extract_page_content(self, url: str) -> Dict:
        """Тухайн хуудасны контентыг задлан авах"""
        try:
            print(f"🔍 Хуудас задлаж байна: {url}")
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Гарчиг авах
            title = ""
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
            
            # Үндсэн контент авах (docs сайтын хэсгээс)
            content = ""
            
            # Янз бүрийн selector-ууд туршиж үзэх
            content_selectors = [
                'main', 'article', '.content', '#content', 
                '.markdown', '.docs-content', '.main-content'
            ]
            
            for selector in content_selectors:
                content_elem = soup.select_one(selector)
                if content_elem:
                    content = content_elem.get_text(separator='\n', strip=True)
                    break
            
            # Хэрэв тодорхой content олдоогүй бол body дотроос авах
            if not content:
                body = soup.find('body')
                if body:
                    # Script, style гэх мэт шаардлагагүй элементүүдийг арилгах
                    for script in body(["script", "style", "nav", "footer", "header"]):
                        script.decompose()
                    content = body.get_text(separator='\n', strip=True)
            
            # Навигацийн линкүүд олох
            links = []
            for link in soup.find_all('a', href=True):
                href = link['href']
                absolute_url = urljoin(url, href)
                if self.is_valid_docs_url(absolute_url):
                    links.append(absolute_url)
            
            return {
                'url': url,
                'title': title,
                'content': content[:5000],  # Хэт урт контентыг хязгаарлах
                'links': list(set(links)),  # Давхцсан линкүүдийг арилгах
                'crawled_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"❌ {url} хуудас задлахад алдаа: {e}")
            return None
    
    def crawl_docs(self, max_pages: int = MAX_CRAWL_PAGES) -> Dict[str, Dict]:
        """CloudMN docs сайтыг crawl хийх"""
        try:
            print(f"🚀 CloudMN docs crawling эхэлж байна... (max: {max_pages} хуудас)")
            
            # Эхлэх URL-ууд
            start_urls = [
                self.base_url,
                urljoin(self.base_url, '/docs/'),
                urljoin(self.base_url, '/getting-started/'),
            ]
            
            urls_to_visit = list(start_urls)
            pages_crawled = 0
            
            while urls_to_visit and pages_crawled < max_pages:
                current_url = urls_to_visit.pop(0)
                
                # Аль хэдийн зочилсон URL эсэхийг шалгах
                if current_url in self.visited_urls:
                    continue
                
                self.visited_urls.add(current_url)
                
                # Хуудасны контентыг авах
                page_data = self.extract_page_content(current_url)
                
                if page_data and page_data['content'].strip():
                    self.docs_content[current_url] = page_data
                    pages_crawled += 1
                    
                    print(f"✅ [{pages_crawled}/{max_pages}] {current_url}")
                    
                    # Шинэ линкүүдийг нэмэх
                    for link in page_data.get('links', []):
                        if link not in self.visited_urls and link not in urls_to_visit:
                            urls_to_visit.append(link)
                
                # Сервертэй зөрүүлэхгүйн тулд түр зогсох
                time.sleep(CRAWL_DELAY)
            
            print(f"🎉 Crawling дууслаа! Нийт {len(self.docs_content)} хуудас цуглуулав")
            return self.docs_content
            
        except Exception as e:
            print(f"❌ Crawling алдаа: {e}")
            return {}

def get_cloudmn_docs_content() -> Dict[str, Dict]:
    """CloudMN документацийн контентыг авах (cache-тэй)"""
    global cloudmn_docs_cache, last_crawl_time
    
    # 1 цагийн дараа дахин crawl хийх
    now = datetime.now()
    if (last_crawl_time is None or 
        (now - last_crawl_time).total_seconds() > 3600 or 
        not cloudmn_docs_cache):
        
        print("🔄 CloudMN docs шинэчилж байна...")
        
        crawler = CloudMNDocsCrawler()
        cloudmn_docs_cache = crawler.crawl_docs()
        last_crawl_time = now
        
        print(f"✅ CloudMN docs cache шинэчлэгдлээ ({len(cloudmn_docs_cache)} хуудас)")
    
    return cloudmn_docs_cache

def create_vector_store() -> FAISS:
    """CloudMN документацийг vector store үүсгэх"""
    global vector_store, last_vector_store_update
    
    try:
        # Хэрэв vector store аль хэдийн үүссэн, 1 цагийн дараа шинэчлэх
        now = datetime.now()
        if (vector_store is not None and 
            last_vector_store_update is not None and 
            (now - last_vector_store_update).total_seconds() < 3600):
            return vector_store
        
        print("🔄 Vector store үүсгэж байна...")
        
        # Документацийн контентыг авах
        docs_content = get_cloudmn_docs_content()
        
        if not docs_content:
            print("❌ Документацийн контент хоосон байна")
            return None
        
        # Документуудыг бэлтгэх
        documents = []
        for url, page_data in docs_content.items():
            title = page_data.get('title', '')
            content = page_data.get('content', '')
            
            if not content.strip():
                continue
            
            # Документуудыг жижиг хэсгүүд болгон хуваах
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200,
                length_function=len,
                separators=["\n\n", "\n", " ", ""]
            )
            
            chunks = text_splitter.split_text(content)
            
            for i, chunk in enumerate(chunks):
                # Мета мэдээлэлтэй Document үүсгэх
                doc = Document(
                    page_content=chunk,
                    metadata={
                        "source": url,
                        "title": title,
                        "chunk_index": i,
                        "total_chunks": len(chunks)
                    }
                )
                documents.append(doc)
        
        print(f"✅ {len(documents)} документ бэлтгэлээ")
        
        # Embeddings үүсгэх
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        
        # Vector store үүсгэх
        vector_store = FAISS.from_documents(documents, embeddings)
        last_vector_store_update = now
        
        print(f"✅ Vector store үүсгэлээ ({len(documents)} документ)")
        
        return vector_store
        
    except Exception as e:
        print(f"❌ Vector store үүсгэхэд алдаа: {e}")
        return None

def search_cloudmn_docs_vector(query: str, max_results: int = MAX_VECTOR_RESULTS) -> List[Dict]:
    """Vector similarity search ашиглан CloudMN документацаас хайлт хийх"""
    try:
        # Vector store үүсгэх эсвэл авах
        vector_store = create_vector_store()
        
        if not vector_store:
            print("❌ Vector store үүсгэх боломжгүй")
            return []
        
        # Хайлт хийх
        docs_and_scores = vector_store.similarity_search_with_score(query, k=max_results)
        
        results = []
        for doc, score in docs_and_scores:
            # Score-г 0-1 хүрээнд хөрвүүлэх
            similarity_score = 1.0 - score  # FAISS-ийн distance-ийг similarity болгох
            
            # Хэрэв similarity хязгаараас бага бол алгасах
            if similarity_score < VECTOR_SIMILARITY_THRESHOLD:
                continue
            
            # Үр дүнг бэлтгэх
            result = {
                'url': doc.metadata.get('source', ''),
                'title': doc.metadata.get('title', ''),
                'content_excerpt': doc.page_content,
                'similarity_score': similarity_score,
                'chunk_index': doc.metadata.get('chunk_index', 0),
                'total_chunks': doc.metadata.get('total_chunks', 0)
            }
            
            results.append(result)
        
        return results
        
    except Exception as e:
        print(f"❌ Vector search алдаа: {e}")
        return []

def enhance_ai_response_with_cloudmn_docs(message_content: str) -> str:
    """Хэрэглэгчийн асуултанд CloudMN документацийн мэдээллийг нэмж өгөх (vector search)"""
    try:
        print(f"🔍 CloudMN документацаас хайлт хийж байна: {message_content[:50]}...")
        
        # Vector search хийх
        search_results = search_cloudmn_docs_vector(message_content)
        
        if not search_results:
            print("❌ Хайлтад тохирох үр дүн олдсонгүй")
            return ""
        
        print(f"✅ {len(search_results)} үр дүн олдлоо")
        
        # AI-д өгөх нэмэлт контекст бэлтгэх
        docs_context = "\n\nCloudMN документацаас олдсон холбогдох мэдээлэл:\n"
        
        for i, result in enumerate(search_results, 1):
            similarity_percent = int(result['similarity_score'] * 100)
            docs_context += f"\n{i}. {result['title']} (Холбоотой байдал: {similarity_percent}%)\n"
            docs_context += f"   URL: {result['url']}\n"
            docs_context += f"   Контент: {result['content_excerpt']}\n"
        
        docs_context += "\nЭнэ мэдээллийг ашиглаж хэрэглэгчийн асуултанд хариулна уу."
        
        return docs_context
        
    except Exception as e:
        print(f"❌ CloudMN docs нэмэхэд алдаа: {e}")
        return ""

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
            "Тухайн асуудлыг чадахаар байвал өөрийн мэдлэгийн хүрээнд шийдвэрлэж өгнө үү. "
            "Хэрэглэгчийн бүх чат түүхийг харж, асуудлыг иж бүрэн дүгнэж өгнө үү. "
            "Хэрэв олон асуудал байвал гол асуудлыг тодорхойлж фокуслана уу."
        )

        # Comprehensive user prompt
        user_msg = f'''
Хэрэглэгчийн чат түүх:
{chat_history}

Одоогийн мессеж: "{current_message}"

Дараах форматаар бүх чат түүхэд тулгуурлан дүгнэлт өгнө үү:

АСУУДЛЫН ТӨРӨЛ: [Техникийн/Мэдээллийн/Гомдол]
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
    """OpenAI Assistant-ээс хариулт авах (CloudMN docs integration-тэй)"""
    try:
        # CloudMN документацийн нэмэлт мэдээлэл авах
        cloudmn_context = enhance_ai_response_with_cloudmn_docs(message_content)
        
        # Хэрэв CloudMN холбоотой мэдээлэл олдвол үүнийг мессежид нэмэх
        enhanced_message = message_content
        if cloudmn_context:
            enhanced_message = message_content + cloudmn_context
            print(f"📚 CloudMN docs контекст нэмэгдлээ ({len(cloudmn_context)} тэмдэгт)")

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

        # Assistant-ийн хариултыг авах
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        
        for msg in messages.data:
            if msg.role == "assistant":
                reply = ""
                for content_block in msg.content:
                    if hasattr(content_block, 'text'):
                        reply += content_block.text.value
                
                # AI хариултыг цэвэрлэх
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

@app.route("/debug-env", methods=["GET"])
def debug_env():
    """Орчны хувьсагчдыг шалгах debug endpoint"""
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
        "VERIFICATION_URL_BASE": VERIFICATION_URL_BASE
    }

def clean_ai_response(response_text):
    """AI хариултыг цэвэрлэж, JSON хэлбэрийг хэрэглэгчдэд ойлгомжтой болгох"""
    try:
        # JSON хэлбэрийг илрүүлэх
        import json
        
        # Хэрэв JSON хэлбэр байвал parse хийх
        if response_text.strip().startswith('{') and response_text.strip().endswith('}'):
            try:
                json_data = json.loads(response_text)
                
                # JSON дотор clarification_question байвал тэрийг буцаах
                if isinstance(json_data, dict):
                    if 'clarification_question' in json_data:
                        return json_data['clarification_question']
                    elif 'message' in json_data:
                        return json_data['message']
                    elif 'content' in json_data:
                        return json_data['content']
                    elif 'response' in json_data:
                        return json_data['response']
                    else:
                        # JSON-ийн утгуудыг нэгтгэж хэрэглэгчдэд ойлгомжтой болгох
                        readable_parts = []
                        for key, value in json_data.items():
                            if isinstance(value, str) and value.strip():
                                readable_parts.append(value)
                        
                        if readable_parts:
                            return ' '.join(readable_parts)
                        else:
                            return "Уучлаарай, тодорхой хариулт өгөх боломжгүй байна. Асуултаа дахин тодорхой асуугаарай."
                            
            except json.JSONDecodeError:
                pass
        
        # JSON биш бол шууд буцаах
        return response_text.strip()
        
    except Exception as e:
        print(f"❌ AI хариулт цэвэрлэхэд алдаа: {e}")
        return response_text.strip()

if __name__ == "__main__":
    app.run(debug=True, port=5000)