import os
import time
import requests
import re
import jwt
import smtplib
import json
import hashlib
import pickle

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

from flask import Flask, request, jsonify, render_template_string
from bs4 import BeautifulSoup
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import FAISS
from langchain.schema import Document
from openai import OpenAI

app = Flask(__name__)

# ===== Орчны хувьсагчид =====
OPENAI_API_KEY     = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID       = os.environ["ASSISTANT_ID"]
CHATWOOT_API_KEY   = os.environ["CHATWOOT_API_KEY"]
ACCOUNT_ID         = os.environ["ACCOUNT_ID"]
CHATWOOT_BASE_URL  = "https://app.chatwoot.com"

SMTP_SERVER        = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT          = int(os.environ.get("SMTP_PORT", "587"))
SENDER_EMAIL       = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD    = os.environ["SENDER_PASSWORD"]

TEAMS_WEBHOOK_URL  = os.environ.get("TEAMS_WEBHOOK_URL")
MAX_AI_RETRIES     = 2  # AI хэдэн удаа retry хийх

JWT_SECRET         = os.environ.get("JWT_SECRET", "your-secret-key-here")
VERIFICATION_URL_BASE = os.environ.get("VERIFICATION_URL_BASE", "http://localhost:5000")

# RAG тохиргоо
RAG_ENABLED        = os.environ.get("RAG_ENABLED", "true").lower() == "true"
VECTOR_STORE_PATH  = "cloudmn_vectorstore"
CRAWL_CACHE_FILE   = "cloudmn_crawl_cache.json"
CRAWL_MAX_PAGES    = int(os.environ.get("CRAWL_MAX_PAGES", "100"))
ESCALATION_THRESHOLD = int(os.environ.get("ESCALATION_THRESHOLD", "3"))

# Глобаль обьект
vector_store = None
embeddings   = None

# Хэрэглэгчийн асуултын түүх хадгалах
user_query_history = {}   # conv_id -> [олон асуулт]
user_last_urls     = {}   # conv_id -> [URL жагсаалт]

# OpenAI клиент
client = OpenAI(api_key=OPENAI_API_KEY)

# ===== RAG системийг эхлүүлэх =====
if RAG_ENABLED:
    try:
        embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        # FAISS локал файлууд: VECTOR_STORE_PATH хавтсан дахь index файлууд
        faiss_index_path = os.path.join(VECTOR_STORE_PATH, "index.faiss")
        if os.path.exists(faiss_index_path):
            vector_store = FAISS.load_local(VECTOR_STORE_PATH, embeddings)
            print("✅ Хадгалагдсан vector store-г ачааллаа")
        else:
            print("⚠️ Vector store олдсонгүй - эхлээд /rag/build дуудна уу")
    except Exception as e:
        print(f"❌ RAG систем эхлүүлэхэд алдаа: {e}")
        RAG_ENABLED = False


# ----- Туслах функцууд -----

def is_valid_email(email: str) -> bool:
    """Имэйл хаягийн форматыг шалгах"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def clean_text(text: str) -> str:
    """Текстийг цэвэрлэх: олон хоосон мөр болон зайг багасгана"""
    text = re.sub(r'\n\s*\n', '\n', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_content_from_url(url: str, visited_urls=None):
    """Нэг URL-аас title, цэвэрхэн текст илрүүлэх"""
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

        title_tag = soup.find('title')
        title_text = title_tag.get_text().strip() if title_tag else "Untitled"

        content_selectors = ['main', 'article', '.content', '#content', '.markdown', '.doc-content', '.documentation']
        content_elem = None
        for selector in content_selectors:
            elem = soup.select_one(selector)
            if elem:
                content_elem = elem
                break

        if not content_elem:
            content_elem = soup.find('body')
        if not content_elem:
            return None

        # script, style, nav, header, footer, aside устгах
        for tag in content_elem(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        text_content = content_elem.get_text()
        text_content = clean_text(text_content)

        if len(text_content) < 50:  # хэт богино контент алгасах
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
    """https://docs.cloud.mn сайтыг crawl хийж cache болгох"""
    base_url    = "https://docs.cloud.mn"
    start_urls  = ["https://docs.cloud.mn/"]
    visited     = set()
    all_docs    = []

    # Хэрэв cache файл байгаа бол түүнээс ачаална
    if os.path.exists(CRAWL_CACHE_FILE):
        try:
            with open(CRAWL_CACHE_FILE, 'r', encoding='utf-8') as f:
                cached = json.load(f)
                print(f"✅ Cache-аас {len(cached)} хуудас ачааллаа")
                return cached
        except Exception as e:
            print(f"⚠️ Cache уншихад алдаа: {e}")

    print(f"🕷️ CloudMN docs crawl эхэлж байна... (Max {CRAWL_MAX_PAGES} хуудас)")

    queue = start_urls.copy()
    page_count = 0

    while queue and page_count < CRAWL_MAX_PAGES:
        cur = queue.pop(0)
        if cur in visited:
            continue

        data = extract_content_from_url(cur, visited)
        if data:
            all_docs.append(data)
            page_count += 1
            print(f"✅ [{page_count}/{CRAWL_MAX_PAGES}] {data['title']}")

            # Доторт линк нэмэх
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                resp = requests.get(cur, headers=headers, timeout=10)
                soup = BeautifulSoup(resp.content, 'html.parser')
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    full = urljoin(cur, href)
                    if (full.startswith(base_url)
                        and full not in visited
                        and full not in queue
                        and not full.endswith(('.pdf', '.jpg', '.png', '.gif', '.css', '.js'))):
                        queue.append(full)
            except Exception as e:
                print(f"⚠️ Линк олоход алдаа {cur}: {e}")

        time.sleep(0.5)

    print(f"✅ Crawl дууслаа: {len(all_docs)} хуудас")

    try:
        with open(CRAWL_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_docs, f, ensure_ascii=False, indent=2)
        print(f"💾 Cache хадгаллаа: {CRAWL_CACHE_FILE}")
    except Exception as e:
        print(f"⚠️ Cache хадгалахад алдаа: {e}")

    return all_docs


def build_vector_store():
    """Vector store үүсгэж, FAISS-ээр хадгалах"""
    global vector_store, embeddings

    if not RAG_ENABLED:
        print("❌ RAG идэвхгүй байна")
        return False

    try:
        print("🔧 Vector store үүсгэж байна...")
        docs_data = crawl_cloudmn_docs()
        if not docs_data:
            print("❌ Crawl хийх документ олдсонгүй")
            return False

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", "! ", "? ", " "]
        )

        all_chunks = []
        for d in docs_data:
            chunks = splitter.split_text(d['content'])
            for idx, txt in enumerate(chunks):
                if len(txt.strip()) > 50:
                    doc = Document(
                        page_content=txt,
                        metadata={
                            'title': d['title'],
                            'url': d['url'],
                            'chunk_id': idx,
                            'total_chunks': len(chunks)
                        }
                    )
                    all_chunks.append(doc)

        print(f"📄 {len(all_chunks)} ширхэг chunk үүсгэлээ")
        if not all_chunks:
            print("❌ Алдартай chunk олдсонгүй")
            return False

        if not embeddings:
            embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)

        print("🔮 Embeddings үүсгэж байна...")
        vector_store = FAISS.from_documents(all_chunks, embeddings)

        # LOCAL SAVE: VECTOR_STORE_PATH хавтас үүсгэнэ
        if not os.path.exists(VECTOR_STORE_PATH):
            os.makedirs(VECTOR_STORE_PATH)
        vector_store.save_local(VECTOR_STORE_PATH)
        print(f"💾 Vector store хадгаллаа: {VECTOR_STORE_PATH}")

        return True

    except Exception as e:
        print(f"❌ Vector store үүсгэхэд алдаа: {e}")
        return False


def search_cloudmn_docs(query: str, k: int = 5):
    """Vector store-аас similarity search хийх"""
    global vector_store

    if not RAG_ENABLED or not vector_store:
        return []

    try:
        results = vector_store.similarity_search(query, k=k)
        out = []
        for doc in results:
            out.append({
                'content': doc.page_content,
                'title': doc.metadata.get('title', 'Unknown'),
                'url': doc.metadata.get('url', ''),
                'chunk_id': doc.metadata.get('chunk_id', 0)
            })
        return out

    except Exception as e:
        print(f"❌ RAG хайлт алдаа: {e}")
        return []


def is_similar_query(query1: str, query2: str) -> bool:
    """GPT-ээр хоёр асуулт ижил төрөл эсэхийг шалгах"""
    try:
        system_msg = (
            "Та бол асуултын ижил төрөл тодорхойлох мэргэжилтэн.\n"
            "Хоёр асуулт ижил төрлийн асуудлын талаар эсэхийг хэлнэ үү.\n"
            "Зөвхөн 'ИЖИЛ' эсвэл 'ӨӨР' гэж хариулна уу."
        )
        user_msg = f"""
        Асуулт 1: "{query1}"
        Асуулт 2: "{query2}"
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
        res_text = response.choices[0].message.content.strip()
        return "ИЖИЛ" in res_text

    except Exception as e:
        print(f"❌ Асуулт харьцуулах алдаа: {e}")
        return False


def should_search_new_content(conv_id: str, current_query: str):
    """Шинэ хайлт хийх эсэхийг шийдэх (conversation-д үзэхгүй байвал True)"""
    if conv_id not in user_query_history:
        user_query_history[conv_id] = [current_query]
        return True, "Анхны асуулт"

    previous = user_query_history[conv_id][-1]
    if previous and is_similar_query(current_query, previous):
        return False, "Ижил төрлийн асуулт"

    user_query_history[conv_id].append(current_query)
    if len(user_query_history[conv_id]) > 10:
        user_query_history[conv_id] = user_query_history[conv_id][-10:]
    return True, "Шинэ төрлийн асуулт"


def should_escalate_to_support(conv_id: str, current_msg: str):
    """Дэмжлэгийн багт явуулах эсэхийг шийдэх (ESCALATION логик)"""
    if not RAG_ENABLED:
        return True, "RAG идэвхгүй"

    count = len(user_query_history.get(conv_id, []))
    if count >= ESCALATION_THRESHOLD:
        return True, f"Олон асуулт гарсан ({count})"

    urgent_kw = ["алдаа гарч", "ажиллахгүй", "буруу", "асуудал", "яаралтай"]
    txt = current_msg.lower()
    if any(kw in txt for kw in urgent_kw):
        return True, "Яаралтай түлхүүр үг олдсон"

    return False, "Хэвийн асуулт"


def generate_verification_token(email: str, conv_id: str, contact_id: str) -> str:
    """JWT токен үүсгэх (24 цагийн хүчинтэй)"""
    payload = {
        'email': email,
        'conv_id': conv_id,
        'contact_id': contact_id,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def verify_token(token: str):
    """JWT токеныг шалгах"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    except Exception:
        return None


def send_verification_email(email: str, token: str) -> bool:
    """Имэйл хаяг баталгаажуулах холбоос илгээх"""
    try:
        verification_url = f"{VERIFICATION_URL_BASE}/verify?token={token}"
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = email
        msg['Subject'] = "Имэйл хаягаа баталгаажуулна уу"

        body = f"""
        Сайн байна уу!
        
        Таны имэйл хаягийг баталгаажуулахын тулд дараах линк дээр дарна уу:
        {verification_url}
        
        Энэ линк 24 цагийн дараа хүчингүй болно.
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


def get_contact(contact_id: str):
    """Chatwoot-с contact авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()


def update_contact(contact_id: str, attrs: dict):
    """Contact-ийн custom_attributes шинэчлэх"""
    try:
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}"
        payload = {"custom_attributes": attrs}
        headers = {"api_access_token": CHATWOOT_API_KEY}
        resp = requests.put(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"❌ Contact update алдаа: {e}")
        raise


def get_conversation(conv_id: str):
    """Chatwoot-с conversation авах"""
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    resp = requests.get(url, headers={"api_access_token": CHATWOOT_API_KEY})
    resp.raise_for_status()
    return resp.json()


def update_conversation(conv_id: str, attrs: dict):
    """Conversation-ийн custom_attributes шинэчлэх"""
    try:
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/custom_attributes"
        payload = {"custom_attributes": attrs}
        headers = {"api_access_token": CHATWOOT_API_KEY}
        resp = requests.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"❌ Conversation update алдаа: {e}")
        raise


def send_to_chatwoot(conv_id: str, text: str):
    """Chatwoot руу мессеж бичих"""
    try:
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
        payload = {"content": text, "message_type": "outgoing"}
        headers = {"api_access_token": CHATWOOT_API_KEY}
        r = requests.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ Message send алдаа: {e}")
        raise


def analyze_customer_issue(thread_id: str, current_message: str, customer_email=None) -> str:
    """AI-ээр хэрэглэгчийн чатын түүхийг дүгнэх"""
    try:
        messages = client.beta.threads.messages.list(thread_id=thread_id, limit=50)
        conversation_history = []
        for msg in reversed(messages.data):
            if msg.role == "user":
                txt = "".join(
                    block.text.value for block in msg.content if hasattr(block, 'text')
                )
                if txt.strip():
                    conversation_history.append(f"Хэрэглэгч: {txt.strip()}")
            elif msg.role == "assistant":
                txt = "".join(
                    block.text.value for block in msg.content if hasattr(block, 'text')
                )
                if txt.strip():
                    conversation_history.append(f"AI: {txt.strip()[:200]}...")

        if not conversation_history:
            conversation_history = [f"Хэрэглэгч: {current_message}"]

        chat_history = "\n".join(conversation_history[-10:])

        system_msg = (
            "Та бол дэмжлэгийн мэргэжилтэн. "
            "Хэрэглэгчийн бүх чат түүхийг харж, асуудлыг иж бүрэн дүгнэнэ үү."
        )
        user_msg = f"""
        Хэрэглэгчийн чат түүх:
        {chat_history}

        Одоогийн мессеж: "{current_message}"

        Дараах форматаар дүгнэлт бичнэ үү:
        АСУУДЛЫН ТӨРӨЛ:
        ЯАРАЛТАЙ БАЙДАЛ:
        АСУУДЛЫН ТОВЧ ТАЙЛБАР:
        ЧАТЫН ХЭВ МАЯГ:
        ШААРДЛАГАТАЙ АРГА ХЭМЖЭЭ:
        ХҮЛЭЭГДЭЖ БУЙ ХАРИУЛТ:
        ДҮГНЭЛТ:
        """
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
        return f"Асуудал дүгнэх боломжгүй: {current_message}"


def send_teams_notification(
    conv_id: str,
    customer_message: str,
    customer_email=None,
    escalation_reason="Хэрэглэгчийн асуудал",
    ai_analysis=None
) -> bool:
    """Microsoft Teams руу мессеж илгээх (Adaptive Card-г хадгалсан)"""
    if not TEAMS_WEBHOOK_URL:
        print("⚠️ Teams webhook URL тохируулаагүй")
        return False

    try:
        conv_url = f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
        error_summary = escalation_reason
        if ai_analysis:
            error_summary += f"\n\nДэлгэрэнгүй анализ:\n{ai_analysis}"

        # ### Adaptive Card Format ###
        teams_message = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": "AI дүгнэлт ирлээ",
            "themeColor": "EA4300",
            "title": "📋 Хэрэглэгчийн асуудлын дүгнэлт",
            "text": (
                f"👤 **Харилцагч:** {customer_email or 'Тодорхойгүй'}\n"
                f"⏱ **Хугацаа:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"💬 **Мессеж:** {customer_message[:200]}"
                + ("..." if len(customer_message) > 200 else "")
            ),
            "sections": [
                {
                    "activityTitle": "🔍 **AI Дүгнэлт**",
                    "text": ai_analysis or "_AI анализ байхгүй_"
                }
            ],
            "potentialAction": [
                {
                    "@type": "OpenUri",
                    "name": "Chatwoot дээр үзэх",
                    "targets": [
                        {"os": "default", "uri": conv_url}
                    ]
                }
            ]
        }

        r = requests.post(TEAMS_WEBHOOK_URL, json=teams_message)
        r.raise_for_status()
        print(f"✅ Teams-д илгээлээ: {escalation_reason}")
        return True

    except Exception as e:
        print(f"❌ Teams мессеж илгээхэд алдаа: {e}")
        return False


def get_ai_response_with_rag(
    thread_id: str,
    message_content: str,
    conv_id=None,
    customer_email=None,
    retry_count=0
) -> str:
    """RAG context-тай AI хариулт авах"""
    global user_last_urls

    try:
        rag_context = ""
        used_urls = []

        if RAG_ENABLED and vector_store and conv_id:
            should_search, reason = should_search_new_content(conv_id, message_content)
            print(f"🔍 Хайлтын шийдвэр: {should_search} - {reason}")

            if should_search:
                results = search_cloudmn_docs(message_content, k=3)
                if results:
                    rag_context = "\n\n**CloudMN docs-аас олдсон мэдээлэл:**\n"
                    new_urls = []
                    for i, res in enumerate(results, start=1):
                        rag_context += (
                            f"{i}. [{res['title']}]({res['url']})\n"
                            f"{res['content'][:200]}...\n\n"
                        )
                        if res['url'] not in new_urls:
                            new_urls.append(res['url'])
                    user_last_urls[conv_id] = new_urls
                    used_urls = new_urls
                    print(f"🔗 Шинэ URL хадгалсан: {len(new_urls)}")
                else:
                    print("❌ Хайлтын үр дүн алга")
            else:
                prev_urls = user_last_urls.get(conv_id, [])
                if prev_urls:
                    rag_context = "\n\n**Өмнө ашигласан CloudMN хуудас:**\n"
                    for idx, u in enumerate(prev_urls[:3], start=1):
                        rag_context += f"{idx}. {u}\n"
                    used_urls = prev_urls[:3]
                else:
                    print("⚠️ Өмнөх URL алга")

        # AI-д дамжуулах текстэнд rag_context нэмэх
        final_input = message_content + rag_context

        # Chatwoot thread-д хэрэглэгчийн мессеж нэмэх
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=final_input)

        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)
        max_wait = 30
        wait = 0
        while wait < max_wait:
            status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if status.status == "completed":
                break
            elif status.status in ["failed", "cancelled", "expired"]:
                err_msg = "Уучлаарай, AI ажилласангүй. Дахин оролдоно уу."
                if retry_count == 0 and conv_id:
                    send_teams_notification(
                        conv_id, message_content, customer_email,
                        f"AI run алдаа: {status.status}",
                        f"Run ID: {run.id}, Status: {status.status}"
                    )
                return err_msg
            time.sleep(1)
            wait += 1

        if wait >= max_wait:
            timeout_msg = "AI хариулт хэтэрхий удааширлаа. Дахин оролдоно уу."
            if retry_count == 0 and conv_id:
                send_teams_notification(
                    conv_id, message_content, customer_email,
                    "AI timeout (30 сек)", f"Run ID: {run.id}"
                )
            return timeout_msg

        msgs = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in msgs.data:
            if msg.role == "assistant":
                text = "".join(
                    block.text.value for block in msg.content if hasattr(block, 'text')
                )
                # Хэрэв URL ашигласан бол төгсгөлд нь нэмэх
                if used_urls:
                    text += "\n\n**CloudMN хуудас харах:**\n"
                    for u in used_urls:
                        text += f"- {u}\n"
                return text

        return "AI хариулт олдсонгүй. Дахин оролдоно уу."

    except Exception as e:
        print(f"❌ AI хариулт алдаа: {e}")
        err_msg = "AI-тэй холбогдох алдаа гарлаа. Дахин оролдоно уу."
        if retry_count == 0 and conv_id:
            send_teams_notification(
                conv_id, message_content, customer_email,
                "AI системийн Exception", str(e)
            )
        return err_msg


@app.route("/verify", methods=["GET"])
def verify_email():
    """Email Verify endpoint"""
    token = request.args.get('token')
    if not token:
        return "❌ Токен байхгүй!", 400

    payload = verify_token(token)
    if not payload:
        return "❌ Токен хүчинтэй биш эсвэл дууссан!", 400

    try:
        contact_id = payload['contact_id']
        email      = payload['email']
        conv_id    = payload.get('conv_id')

        update_contact(contact_id, {
            "email_verified": "1",
            "verified_email": email,
            "verification_date": datetime.utcnow().isoformat()
        })

        if conv_id:
            send_to_chatwoot(conv_id, f"✅ Таны имэйл ({email}) баталгаажлаа!")

        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>Имэйл баталлаа</title></head>
        <body style="font-family:Arial,sans-serif;text-align:center;padding:50px;">
            <h2 style="color:green;">✅ Баталгаажлаа!</h2>
            <p>Таны имэйл хаяг ({{ email }}) баталгаажсан.</p>
        </body>
        </html>
        """, email=email)

    except Exception as e:
        print(f"❌ Баталгаажуулахад алдаа: {e}")
        return f"❌ Баталгаажуулахад алдаа: {e}", 500


@app.route("/webhook", methods=["POST"])
def webhook():
    """Chatwoot webhook handler"""
    try:
        data = request.json
        if data.get("message_type") != "incoming":
            return jsonify({"status": "skipped - not incoming"}), 200

        conv_id         = data["conversation"]["id"]
        message_content = data.get("content", "").strip()

        contact_id = data.get("sender", {}).get("id")
        if not contact_id:
            send_to_chatwoot(conv_id, "❌ Contact ID олдсонгүй")
            return jsonify({"status": "error - no contact"}), 400

        # Баталгаажуулалт шалгах
        is_verified   = False
        verified_email = ""
        meta_sender = data.get("conversation", {}).get("meta", {}).get("sender", {})
        if "custom_attributes" in meta_sender:
            attrs = meta_sender["custom_attributes"]
            if str(attrs.get("email_verified", "")).lower() in ["true", "1", "yes"]:
                is_verified = True
                verified_email = attrs.get("verified_email", "")

        if not is_verified:
            # API-аар дахин шалгах
            try:
                contact = get_contact(contact_id)
                attrs = contact.get("custom_attributes", {})
                if str(attrs.get("email_verified", "")).lower() in ["true", "1", "yes"]:
                    is_verified = True
                    verified_email = attrs.get("verified_email", "")
            except Exception:
                is_verified = False

        if not is_verified:
            # Хэрэв мессеж нь имэйл маягтай бол баталгаажуулалтын линк илгээх
            if is_valid_email(message_content):
                token = generate_verification_token(message_content, conv_id, contact_id)
                if send_verification_email(message_content, token):
                    send_to_chatwoot(conv_id,
                        f"📧 Таны имэйл ({message_content}) рүү баталгаажуулалтын линк илгээлээ.\n"
                        f"Линк 24 цагийн дараа хүчингүй болно.")
                else:
                    send_to_chatwoot(conv_id, "❌ Имэйл илгээхэд алдаа гарлаа.")
            else:
                send_to_chatwoot(conv_id,
                    "👋 Chatbot ашиглахын тулд эхлээд имэйлээ баталгаажуулна уу.\n"
                    "📧 Имэйлээ бичнэ үү (Жишээ: example@gmail.com)")
            return jsonify({"status": "waiting_verification"}), 200

        # ===== Баталгаажсан хэрэглэгч =====
        conv = get_conversation(conv_id)
        attrs = conv.get("custom_attributes", {})
        thread_key = f"openai_thread_{contact_id}"
        thread_id  = attrs.get(thread_key)

        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            update_conversation(conv_id, {thread_key: thread_id})

        # AI хариулт олж авах
        retry_count = 0
        ai_response = None
        while retry_count <= MAX_AI_RETRIES:
            ai_response = get_ai_response_with_rag(thread_id, message_content, conv_id, verified_email, retry_count)
            if not any(err in ai_response for err in ["алдаа", "хэт удаа", "олдсонгүй"]):
                break
            retry_count += 1
            if retry_count <= MAX_AI_RETRIES:
                time.sleep(2)

        if retry_count > MAX_AI_RETRIES:
            # AI алдаа бол ажилтанд redirect
            send_teams_notification(
                conv_id,
                message_content,
                verified_email,
                f"AI {MAX_AI_RETRIES+1} retry алдаа",
                f"Thread ID: {thread_id}"
            )
            ai_response = (
                "🚨 Уучлаарай, техникийн алдаа гарлаа.\n"
                "Таны асуултыг ажилтанд дамжуулаа.\n"
                "🕐 Түр хүлээнэ үү..."
            )

        send_to_chatwoot(conv_id, ai_response)

        # AI амжилттай бол escalate шалгах
        if retry_count <= MAX_AI_RETRIES:
            should_esc, reason = should_escalate_to_support(conv_id, message_content)
            if should_esc:
                analysis = analyze_customer_issue(thread_id, message_content, verified_email)
                send_teams_notification(conv_id, message_content, verified_email, f"Асуудал: {reason}", analysis)

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"💥 Webhook алдаа: {e}")
        return jsonify({"status": f"error: {e}"}), 500


@app.route("/test-teams", methods=["GET"])
def test_teams():
    """Teams Webhook тест хийх"""
    if not TEAMS_WEBHOOK_URL:
        return jsonify({"error": "TEAMS_WEBHOOK_URL тохируулаагүй"}), 400
    try:
        test_analysis = "АСУУДЛЫН ТӨРӨЛ: Тест\nЯАРАЛТАЙ: Бага\nТайлбар: Систем ажиллаж байна"
        success = send_teams_notification(
            conv_id="test_123",
            customer_message="Энэ тест мэдээлэл.",
            customer_email="test@example.com",
            escalation_reason="Teams тест",
            ai_analysis=test_analysis
        )
        if success:
            return jsonify({"status": "success", "message": "Teams-д илгээлээ"}), 200
        else:
            return jsonify({"error": "Teams илгээхэд алдаа"}), 500
    except Exception as e:
        return jsonify({"error": f"{e}"}), 500


@app.route("/debug-env", methods=["GET"])
def debug_env():
    """Орчны хувьсагч шалгах"""
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
        "cached_pages": len(json.load(open(CRAWL_CACHE_FILE))) if os.path.exists(CRAWL_CACHE_FILE) else 0,
        "vector_store_exists": vector_store is not None
    }


@app.route("/rag/build", methods=["POST"])
def build_rag():
    """RAG vector store үүсгэх буцах боломжтой endpoint"""
    if not RAG_ENABLED:
        return jsonify({"error": "RAG идэвхгүй"}), 400
    success = build_vector_store()
    if success:
        return jsonify({
            "status": "success",
            "message": "Vector store амжилттай үүсгэлээ",
            "vector_store_path": VECTOR_STORE_PATH
        }), 200
    return jsonify({"error": "Vector store үүсгэхэд алдаа"}), 500


@app.route("/rag/search", methods=["POST"])
def search_rag():
    """RAG хайлт хийх (тест)"""
    if not RAG_ENABLED:
        return jsonify({"error": "RAG идэвхгүй"}), 400
    if not vector_store:
        return jsonify({"error": "Vector store үүсээгүй. /rag/build ашигла"}), 400

    data = request.json or {}
    query = data.get("query", "").strip()
    k     = data.get("k", 5)
    if not query:
        return jsonify({"error": "Query заавал заавал"}), 400

    results = search_cloudmn_docs(query, k=k)
    return jsonify({
        "status": "success",
        "query": query,
        "results_count": len(results),
        "results": results
    }), 200


@app.route("/rag/status", methods=["GET"])
def rag_status():
    """RAG статусыг шалгах"""
    status = {
        "rag_enabled": RAG_ENABLED,
        "vector_store_exists": vector_store is not None,
        "vector_store_path": VECTOR_STORE_PATH,
        "cache_exists": os.path.exists(CRAWL_CACHE_FILE),
        "max_crawl_pages": CRAWL_MAX_PAGES
    }
    if status["cache_exists"]:
        try:
            cached = json.load(open(CRAWL_CACHE_FILE, encoding='utf-8'))
            status["cached_pages"] = len(cached)
        except:
            status["cached_pages"] = "Unknown"
    faiss_file = os.path.join(VECTOR_STORE_PATH, "index.faiss")
    status["vector_files"] = {
        "faiss_exists": os.path.exists(faiss_file)
    }
    return jsonify(status), 200


@app.route("/rag/refresh", methods=["POST"])
def refresh_rag():
    """Cache устгаж, vector store шинэчлэх"""
    if not RAG_ENABLED:
        return jsonify({"error": "RAG идэвхгүй"}), 400

    # Cache файл устгах
    if os.path.exists(CRAWL_CACHE_FILE):
        os.remove(CRAWL_CACHE_FILE)
        print(f"🗑️ Cache устгалаа: {CRAWL_CACHE_FILE}")
    # Vector store файлууд устгах
    for ext in ["index.faiss", "docstore.pkl"]:
        fpath = os.path.join(VECTOR_STORE_PATH, ext)
        if os.path.exists(fpath):
            os.remove(fpath)
            print(f"🗑️ Vector файлыг устгалаа: {fpath}")

    success = build_vector_store()
    if success:
        return jsonify({
            "status": "success",
            "message": "RAG систем шинэчлэгдлээ"
        }), 200
    return jsonify({"error": "Шинэчилж чадсангүй"}), 500


@app.route("/rag/test-query", methods=["POST"])
def test_query_logic():
    """RAG логикын тест хийх (conversation history-тэй)"""
    if not RAG_ENABLED:
        return jsonify({"error": "RAG идэвхгүй"}), 400
    data = request.json or {}
    conv_id = data.get("conv_id", "test_conv")
    query   = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Query заавал"}), 400

    should_search, reason        = should_search_new_content(conv_id, query)
    should_escalate, esc_reason  = should_escalate_to_support(conv_id, query)
    history_list = user_query_history.get(conv_id, [])
    urls_list    = user_last_urls.get(conv_id, [])

    resp = {
        "status": "success",
        "conv_id": conv_id,
        "query": query,
        "should_search": should_search,
        "search_reason": reason,
        "should_escalate": should_escalate,
        "escalate_reason": esc_reason,
        "query_history": history_list,
        "saved_urls": urls_list,
        "total_queries": len(history_list)
    }

    if should_search and vector_store:
        sr = search_cloudmn_docs(query, k=2)
        resp["search_results_count"] = len(sr)
        resp["search_urls"] = [r['url'] for r in sr]

    return jsonify(resp), 200


@app.route("/rag/clear-history", methods=["POST"])
def clear_history():
    """Асуултын түүх, URL-ыг устгах"""
    data   = request.json or {}
    conv_id = data.get("conv_id", "all")
    if conv_id == "all":
        qc = len(user_query_history)
        uc = len(user_last_urls)
        user_query_history.clear()
        user_last_urls.clear()
        return jsonify({
            "status": "success",
            "message": "Бүх түүх устлаа",
            "conversations_cleared": qc,
            "urls_cleared": uc
        }), 200
    qc = len(user_query_history.get(conv_id, []))
    uc = len(user_last_urls.get(conv_id, []))
    user_query_history.pop(conv_id, None)
    user_last_urls.pop(conv_id, None)
    return jsonify({
        "status": "success",
        "message": f"Conversation {conv_id} түүх устлаа",
        "queries_removed": qc,
        "urls_removed": uc
    }), 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
