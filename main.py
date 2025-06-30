import os
import time
import logging
import requests
from openai import OpenAI
import json
from urllib.parse import urljoin, urlparse
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
import random
from typing import Dict, Optional

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# —— Config —— #
ROOT_URL             = os.getenv("ROOT_URL", "https://docs.cloud.mn/")
DELAY_SEC            = float(os.getenv("DELAY_SEC", "0.5"))
ALLOWED_NETLOC       = urlparse(ROOT_URL).netloc
MAX_CRAWL_PAGES      = int(os.getenv("MAX_CRAWL_PAGES", "50"))
CHATWOOT_API_KEY     = os.getenv("CHATWOOT_API_KEY")
ACCOUNT_ID           = os.getenv("ACCOUNT_ID")
# CHATWOOT_BASE_URL    = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com")
CHATWOOT_BASE_URL    = os.getenv("CHATWOOT_BASE_URL", "https://chat.cloud.mn")
OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY")
AUTO_CRAWL_ON_START  = os.getenv("AUTO_CRAWL_ON_START", "true").lower() == "true"

# SMTP тохиргоо
SMTP_SERVER          = os.getenv("SMTP_SERVER")
SMTP_PORT            = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME        = os.getenv("SENDER_EMAIL")
SMTP_PASSWORD        = os.getenv("SENDER_PASSWORD")
SMTP_FROM_EMAIL      = os.getenv("SENDER_EMAIL")

# Microsoft Teams webhook
TEAMS_WEBHOOK_URL    = os.getenv("TEAMS_WEBHOOK_URL")

# Microsoft Planner тохиргоо
PLANNER_TENANT_ID    = os.getenv("PLANNER_TENANT_ID")
PLANNER_CLIENT_ID    = os.getenv("PLANNER_CLIENT_ID")
PLANNER_CLIENT_SECRET = os.getenv("PLANNER_CLIENT_SECRET")
PLANNER_PLAN_ID      = os.getenv("PLANNER_PLAN_ID")
PLANNER_BUCKET_ID    = os.getenv("PLANNER_BUCKET_ID")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# —— Memory Storage —— #
conversation_memory = {}
crawled_data = []
crawl_status = {"status": "not_started", "message": "Crawling has not started yet"}

# —— Microsoft Planner Integration —— #
_cached_token = None
_token_expiry = 0  # UNIX timestamp

def get_planner_access_token() -> Optional[str]:
    """Microsoft Planner-ийн access token авах"""
    global _cached_token, _token_expiry

    # Хэрвээ token хүчинтэй байвал cache-аас буцаана
    if _cached_token and time.time() < _token_expiry - 10:
        return _cached_token

    url = f"https://login.microsoftonline.com/{PLANNER_TENANT_ID}/oauth2/v2.0/token"
    headers = { "Content-Type": "application/x-www-form-urlencoded" }
    data = {
        "client_id": PLANNER_CLIENT_ID,
        "client_secret": PLANNER_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials"
    }

    try:
        response = requests.post(url, headers=headers, data=data, timeout=10)
        if response.status_code != 200:
            logging.error(f"Planner access token авахад алдаа: {response.status_code} - {response.text}")
            return None

        token_data = response.json()
        _cached_token = token_data["access_token"]
        _token_expiry = time.time() + token_data.get("expires_in", 3600)
        
        logging.info("Planner access token амжилттай авлаа")
        return _cached_token
        
    except Exception as e:
        logging.error(f"Planner access token авахад алдаа гарлаа: {e}")
        return None

class MicrosoftPlannerAPI:
    def __init__(self, access_token: str):
        self.base_url = "https://graph.microsoft.com/v1.0"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

    def create_task(self, plan_id: str, bucket_id: str, title: str,
                    due_date: Optional[str] = None, priority: int = 5,
                    assigned_user_id: Optional[str] = None) -> Dict:
        """Microsoft Planner-д шинэ task үүсгэх"""
        url = f"{self.base_url}/planner/tasks/"
        data = {
            "planId": plan_id,
            "bucketId": bucket_id,
            "title": title,
            "priority": priority
        }

        if due_date:
            data["dueDateTime"] = due_date
    
        # Bulgantamir-ийг үргэлж нэмэх
        bulgantamir_user_id = "c64d22c4-5210-4132-8ad3-776ce1996b6c"
        assignments = {
            bulgantamir_user_id: {
                "@odata.type": "#microsoft.graph.plannerAssignment",
                "orderHint": " !"
            }
        }
        
        # Хэрэв нэмэлт хүн байвал тэрийг бас нэмэх
        if assigned_user_id and assigned_user_id != bulgantamir_user_id:
            assignments[assigned_user_id] = {
                "@odata.type": "#microsoft.graph.plannerAssignment",
                "orderHint": " !"
            }
        
        data["assignments"] = assignments

        try:
            response = requests.post(url, headers=self.headers, json=data, timeout=10)
            return response.json()
        except Exception as e:
            logging.error(f"Planner task үүсгэхэд алдаа гарлаа: {e}")
            return {"error": str(e)}

def create_planner_task(email: str, issue: str, conv_id: Optional[int] = None) -> bool:
    """Microsoft Planner-д task үүсгэх"""
    if not all([PLANNER_TENANT_ID, PLANNER_CLIENT_ID, PLANNER_CLIENT_SECRET, PLANNER_PLAN_ID, PLANNER_BUCKET_ID]):
        logging.error("Microsoft Planner тохиргоо дутуу байна")
        return False
        
    try:
        # Access token авах
        token = get_planner_access_token()
        if not token:
            logging.error("Planner access token авч чадсангүй")
            return False
            
        # Planner API instance үүсгэх
        planner = MicrosoftPlannerAPI(token)
        
        # Task title үүсгэх (buten.py форматтайгаар)
        issue_preview = issue[:50] + "..." if len(issue) > 50 else issue
        title = f"{email} --> {issue_preview}"
        
        # Task үүсгэх (bulgantamir автоматаар нэмэгдэнэ)
        result = planner.create_task(
            plan_id=PLANNER_PLAN_ID or "",
            bucket_id=PLANNER_BUCKET_ID or "",
            title=title,
            priority=1  # Өндөр ач холбогдол
        )
        
        if "error" not in result and result.get("id"):
            task_id = result.get("id")
            logging.info(f"Microsoft Planner task амжилттай үүсгэлээ: {task_id} - {email} (bulgantamir@fibo.cloud assigned)")
            return True
        else:
            logging.error(f"Planner task үүсгэх амжилтгүй: {result}")
            return False
            
    except Exception as e:
        logging.error(f"Planner task үүсгэхэд алдаа гарлаа: {e}")
        return False

# —— Crawl & Scrape —— #
def crawl_and_scrape(start_url: str):
    visited = set()
    to_visit = {start_url}
    results = []

    while to_visit and len(visited) < MAX_CRAWL_PAGES:
        url = to_visit.pop()
        if url in visited:
            continue
        visited.add(url)

        try:
            logging.info(f"[Crawling] {url}")
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            logging.warning(f"Failed to fetch {url}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        body, images = extract_content(soup, url)

        results.append({
            "url": url,
            "title": title,
            "body": body,
            "images": images
        })

        for a in soup.find_all("a", href=True):
            if isinstance(a, Tag):
                href = a.get("href")
                if isinstance(href, str) and is_internal_link(href):
                    full = normalize_url(url, href)
                    if full.startswith(ROOT_URL) and full not in visited:
                        to_visit.add(full)

        time.sleep(DELAY_SEC)

    return results

# —— Startup Functions —— #
def auto_crawl_on_startup():
    """Automatically crawl the site on startup"""
    global crawled_data, crawl_status
    
    if not AUTO_CRAWL_ON_START:
        crawl_status = {"status": "disabled", "message": "Auto-crawl is disabled"}
        logging.info("Auto-crawl is disabled")
        return
    
    try:
        logging.info(f"🚀 Starting automatic crawl of {ROOT_URL}")
        crawl_status = {"status": "running", "message": f"Crawling {ROOT_URL}..."}
        
        crawled_data = crawl_and_scrape(ROOT_URL)
        
        if crawled_data:
            crawl_status = {
                "status": "completed", 
                "message": f"Successfully crawled {len(crawled_data)} pages",
                "pages_count": len(crawled_data),
                "timestamp": datetime.now().isoformat()
            }
            logging.info(f"✅ Auto-crawl completed: {len(crawled_data)} pages")
        else:
            crawl_status = {"status": "failed", "message": "No pages were crawled"}
            logging.warning("❌ Auto-crawl failed: No pages found")
            
    except Exception as e:
        crawl_status = {"status": "error", "message": f"Crawl error: {str(e)}"}
        logging.error(f"❌ Auto-crawl error: {e}")

# Start auto-crawl in background when app starts
import threading
if AUTO_CRAWL_ON_START:
    threading.Thread(target=auto_crawl_on_startup, daemon=True).start()

# —— Content Extraction —— #
def extract_content(soup: BeautifulSoup, base_url: str):
    main_element = soup.find("main")
    main = main_element if main_element else soup
    texts = []
    images = []

    if hasattr(main, 'find_all'):
        for tag in main.find_all(True):  # type: ignore
            if isinstance(tag, Tag) and tag.name in ["h1", "h2", "h3", "h4", "p", "li", "code"]:
                text = tag.get_text(strip=True)
                if text:
                    texts.append(text)

        for img in main.find_all("img"):  # type: ignore
            if isinstance(img, Tag):
                src = img.get("src")
                alt = img.get("alt", "")
                if isinstance(alt, str):
                    alt = alt.strip()
                if src and isinstance(src, str):
                    full_img_url = urljoin(base_url, src)
                    entry = f"[Image] {alt} — {full_img_url}" if alt else f"[Image] {full_img_url}"
                    texts.append(entry)
                    images.append({"url": full_img_url, "alt": alt})

    return "\n\n".join(texts), images

def is_internal_link(href: str) -> bool:
    if not href:
        return False
    parsed = urlparse(href)
    return not parsed.netloc or parsed.netloc == ALLOWED_NETLOC

def normalize_url(base: str, link: str) -> str:
    return urljoin(base, link.split("#")[0])

def scrape_single(url: str):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    body, images = extract_content(soup, url)
    return {"url": url, "title": title, "body": body, "images": images}


# —— AI Assistant Functions —— #
def get_ai_response(user_message: str, conversation_id: int, context_data: Optional[list] = None):
    """Enhanced AI response with better context awareness"""
    
    if not client:
        return "🔑 OpenAI API түлхүүр тохируулагдаагүй байна. Админтай холбогдоно уу."
    
    # Get conversation history
    history = conversation_memory.get(conversation_id, [])
    
    # Build context from crawled data if available
    context = ""
    if crawled_data:
        # Search for relevant content
        search_results = search_in_crawled_data(user_message, max_results=3)
        if search_results:
            relevant_pages = []
            for result in search_results:
                relevant_pages.append(
                    f"Хуудас: {result['title']}\n"
                    f"URL: {result['url']}\n"
                    f"Холбогдох агуулга: {result['snippet']}\n"
                )
            context = "\n\n".join(relevant_pages)
    
    # Build system message with context
    system_content = """Та Cloud.mn-ийн баримт бичгийн талаар асуултад хариулдаг Монгол AI туслах юм. 
    Хэрэглэгчтэй монгол хэлээр ярилцаарай. Хариултаа товч бөгөөд ойлгомжтой байлгаарай.
    
    ЭНГИЙН МЭНДЧИЛГЭЭНИЙ ТУХАЙ:
    Хэрэв хэрэглэгч энгийн мэндчилгээ хийж байвал (жишээ: "сайн байна уу", "сайн уу", "мэнд", "hello", "hi", "сайн уу байна", "hey", "sn bnu", "snu" гэх мэт), дараах байдлаар хариулаарай:
    
    "Сайн байна уу! 👋 Би Cloud.mn-ийн AI туслах юм. Танд хэрхэн туслах вэ?
    
    Би дараах зүйлсээр танд туслаж чадна:
    • 📚 Cloud.mn баримт бичгээс мэдээлэл хайх
    • ❓ Техникийн асуултад хариулах  
    • 💬 Ерөнхий зөвлөгөө өгөх
    
    Асуултаа чөлөөтэй асуугаарай!"
    
    ЯРИЛЦЛАГА ДУУСГАХ ҮГИЙН ТУХАЙ:
    Хэрэв хэрэглэгч ярилцлагыг дуусгах үг хэлвэл (жишээ: "баярлалаа", "zaa bayrlalaa", "баярлаа", "баяртай", "баяртай бна", "thanks", "thank you", "bye", "баяртай" гэх мэт), дараах байдлаар хариулаарай:
    
    "Баярлалаа! 😊. 
    
    Хэрэв дахин асуулт гарвал чөлөөтэй холбогдоорой. Амжилт хүсье! 👋"
    
    Хариулахдаа дараах зүйлсийг анхаарна уу:
    1. Хариултаа холбогдох баримт бичгийн линкээр дэмжүүлээрэй
    2. Хэрэв ойлгомжгүй бол тодорхой асууна уу
    3. Хариултаа бүтэцтэй, цэгцтэй байлгаарай
    4. Техникийн нэр томъёог монгол хэлээр тайлбарлаарай
    
    Хэрэглэгчийн хүсэлтийг автоматаар таньж, дараах үйлдлүүдийг хийх боломжтой:
    - Хэрэглэгч мэдээлэл хайхыг хүсвэл, холбогдох мэдээллийг хайж олж хариулна
    - Хэрэглэгч тодорхой хуудсыг шүүрдэхийг хүсвэл, тухайн хуудсыг шүүрдэж хариулна
    - Хэрэглэгч тусламж хүсвэл, боломжтой үйлдлүүдийн талаар тайлбарлана
    - Хэрэглэгч бүх сайтыг шүүрдэхийг хүсвэл, шүүрдэлтийг эхлүүлнэ"""
    
    if context:
        system_content += f"\n\nКонтекст мэдээлэл:\n{context}"
    
    # Build conversation context
    messages = [
        {
            "role": "system", 
            "content": system_content
        }
    ]
    
    # Add conversation history
    for msg in history[-4:]:  # Last 4 messages
        messages.append(msg)
    
    # Add current message
    messages.append({"role": "user", "content": user_message})
    
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=messages,  # type: ignore
            max_tokens=500,  # Increased token limit for better responses
            temperature=0.7
        )
        
        ai_response = response.choices[0].message.content
        
        # Store in memory
        if conversation_id not in conversation_memory:
            conversation_memory[conversation_id] = []
        
        conversation_memory[conversation_id].append({"role": "user", "content": user_message})
        conversation_memory[conversation_id].append({"role": "assistant", "content": ai_response or ""})
        
        # Keep only last 8 messages
        if len(conversation_memory[conversation_id]) > 8:
            conversation_memory[conversation_id] = conversation_memory[conversation_id][-8:]
            
        return ai_response or "Хариулт авахад алдаа гарлаа."
        
    except Exception as e:
        logging.error(f"OpenAI API алдаа: {e}")
        return f"🔧 AI-тай холбогдоход саад гарлаа. Дараах зүйлсийг туршиж үзнэ үү:\n• Асуултаа дахин илгээнэ үү\n• Асуултаа тодорхой болгоно уу\n• Холбогдох мэдээллийг хайж үзнэ үү\n\nАлдааны дэлгэрэнгүй: {str(e)[:100]}"

def search_in_crawled_data(query: str, max_results: int = 3):
    """Simple search through crawled data"""
    if not crawled_data:
        return []
    
    query_lower = query.lower()
    results = []
    
    for page in crawled_data:
        title = page['title'].lower()
        body = page['body'].lower()
        
        # Check if query matches in title or body
        if (query_lower in title or 
            query_lower in body or 
            any(word in title or word in body for word in query_lower.split())):
            
            # Find the most relevant snippet
            query_words = query_lower.split()
            best_snippet = ""
            max_context = 300
            
            for word in query_words:
                if word in body.lower():
                    start = max(0, body.lower().find(word) - 100)
                    end = min(len(body), body.lower().find(word) + 200)
                    snippet = body[start:end]
                    if len(snippet) > len(best_snippet):
                        best_snippet = snippet
            
            if not best_snippet:
                best_snippet = body[:max_context] + "..." if len(body) > max_context else body
                
            results.append({
                'title': page['title'],
                'url': page['url'],
                'snippet': best_snippet
            })
            
            # Stop when we have enough results
            if len(results) >= max_results:
                break
            
    return results

# def scrape_single(url: str):
#     resp = requests.get(url, timeout=10)
#     resp.raise_for_status()
#     soup = BeautifulSoup(resp.text, "html.parser")
#     title = soup.title.string.strip() if soup.title else url
#     body, images = extract_content(soup, url)
#     return {"url": url, "title": title, "body": body, "images": images}


# —— Enhanced Chatwoot Integration —— #
def send_to_chatwoot(conv_id: int, content: str, message_type: str = "outgoing"):
    """Enhanced chatwoot message sending with better error handling"""
    api_url = (
        f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}"
        f"/conversations/{conv_id}/messages"
    )
    headers = {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "content": content, 
        "message_type": message_type,
        "private": False
    }
    
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        logging.info(f"Message sent to conversation {conv_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to send message to chatwoot: {e}")
        return False

def get_conversation_info(conv_id: int):
    """Get conversation details from Chatwoot"""
    api_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logging.error(f"Failed to get conversation info: {e}")
        return None

def mark_conversation_resolved(conv_id: int):
    """Mark conversation as resolved"""
    api_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/toggle_status"
    headers = {"api_access_token": CHATWOOT_API_KEY}
    payload = {"status": "resolved"}
    
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logging.error(f"Failed to mark conversation as resolved: {e}")
        return False


# —— Microsoft Teams Integration —— #
def send_to_teams(email: str, issue: str, conv_id: Optional[int] = None) -> bool:
    """Send issue to Microsoft Teams via webhook"""
    if not TEAMS_WEBHOOK_URL:
        logging.error("Teams webhook URL not configured")
        return False
    
    # Build Chatwoot conversation link
    chatwoot_link = f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}" if conv_id else "Линк байхгүй"
        
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0076D7",
        "summary": f"Шинэ хүсэлт: {email}",
        "sections": [{
            "activityTitle": "Cloud.mn - Шинэ хүсэлт",
            # "activitySubtitle": f"Хэрэглэгч: {email}",
            "activityImage": "https://docs.cloud.mn/logo.png",
            "facts": [{
                "name": "Хэрэглэгч:",
                "value": email
            }, {
                "name": "Асуудал:",
                "value": issue
            }, {
                "name": "Chatwoot ярилцлага:",
                "value": f"[Ярилцлага харах]({chatwoot_link})"
            }, {
                "name": "Огноо:",
                "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }],
            "markdown": True
        }],
        "potentialAction": [{
            "@type": "OpenUri",
            "name": "Chatwoot-д харах",
            "targets": [{
                "os": "default",
                "uri": chatwoot_link
            }]
        }]
    }
    
    try:
        response = requests.post(
            TEAMS_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        logging.info(f"Issue sent to Teams for {email} with conv link: {chatwoot_link}")
        return True
    except Exception as e:
        logging.error(f"Failed to send to Teams: {e}")
        return False


# —— API Endpoints —— #
@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    data = request.get_json(force=True)
    url = data.get("url")
    if not url:
        return jsonify({"error": "Missing 'url' in JSON body"}), 400
    try:
        page = scrape_single(url)
        return jsonify(page)
    except Exception as e:
        return jsonify({"error": f"Fetch/Scrape failed: {e}"}), 502

@app.route("/api/crawl", methods=["POST"])
def api_crawl():
    pages = crawl_and_scrape(ROOT_URL)
    return jsonify(pages)


# —— Enhanced Chatwoot Webhook —— #
@app.route("/webhook/chatwoot", methods=["POST"])
def chatwoot_webhook():
    """Enhanced webhook with AI integration and assignment checking"""
    global crawled_data, crawl_status
    
    data = request.json or {}
    
    # Only process incoming messages
    if data.get("message_type") != "incoming":
        return jsonify({}), 200

    conv_id = data["conversation"]["id"]
    text = data.get("content", "").strip()
    contact = data.get("conversation", {}).get("contact", {})
    contact_name = contact.get("name", "Хэрэглэгч")
    
    logging.info(f"Received message from {contact_name} in conversation {conv_id}: {text}")
    
    # Check if conversation is assigned to an agent via API call
    logging.info(f"Checking conversation assignment for {conv_id}")
    conv_info = get_conversation_info(conv_id)
    should_respond = True
    
    if conv_info:
        logging.info(f"Got conversation info for {conv_id}: assignee_id={conv_info.get('assignee_id')}")
        
        # Check for assignee in meta first
        assignee_id = None
        assignee_name = "Unknown"
        
        # Try to get assignee from meta.assignee
        meta = conv_info.get("meta", {})
        assignee = meta.get("assignee")
        if assignee:
            assignee_id = assignee.get("id")
            assignee_name = assignee.get("name", "Unknown")
            logging.info(f"Found assignee in meta: ID={assignee_id}, Name={assignee_name}")
        
        # If not found in meta, try direct assignee_id field
        if assignee_id is None:
            assignee_id = conv_info.get("assignee_id")
            if assignee_id and conv_info.get("assignee"):
                assignee_name = conv_info["assignee"].get("name", "Unknown")
                logging.info(f"Found assignee in direct field: ID={assignee_id}, Name={assignee_name}")
        
        # Check if assigned to a human agent (not the bot itself)
        if assignee_id is not None:
            # Log assignment details for debugging
            logging.info(f"Conversation {conv_id} assignee details - ID: {assignee_id}, Name: {assignee_name}")
            
            # Only skip if assigned to a human agent (not if it's self-assigned or auto-assigned)
            # You might need to adjust this logic based on your Chatwoot setup
            if str(assignee_id) != "0" and assignee_name.lower() not in ["bot", "cloudmn bot", "ai assistant"]:
                logging.info(f"🚫 Conversation {conv_id} is assigned to human agent {assignee_name} (ID: {assignee_id}), bot will not respond")
                should_respond = False
            else:
                logging.info(f"✅ Conversation {conv_id} is assigned to bot/system (ID: {assignee_id}), bot will respond")
        else:
            logging.info(f"✅ Conversation {conv_id} is not assigned to any agent, bot will respond")
    else:
        logging.warning(f"⚠️ Could not get conversation info for {conv_id}, assuming bot should respond")
    
    if not should_respond:
        logging.info(f"🚫 Bot will NOT respond to conversation {conv_id} due to assignment")
        return jsonify({"status": "assigned_to_agent"}), 200
    
    logging.info(f"✅ Bot WILL respond to conversation {conv_id}")
    
    # Get conversation history
    history = conversation_memory.get(conv_id, [])
    
    # Check if this is an email address
    logging.info(f"Checking if message contains email: '{text}' (contains @: {'@' in text})")
    
    if "@" in text and is_valid_email(text.strip()):
        logging.info(f"✅ Email detected and validated in conversation {conv_id}: {text.strip()}")
        
        # Store email for confirmation
        if conv_id not in conversation_memory:
            conversation_memory[conv_id] = []
        conversation_memory[conv_id].append({
            "role": "system", 
            "content": f"pending_email:{text.strip()}"
        })
        
        response = f"📧 Таны оруулсан имэйл хаяг: {text.strip()}\n\nТа дахин шалгана уу, зөв бол 'y' буруу бол 'n' гэж бичнэ үү."
        
        logging.info(f"Sending email confirmation message to conversation {conv_id}: {response[:50]}...")
        
        # Send message and check for success
        send_success = send_to_chatwoot(conv_id, response)
        if send_success:
            logging.info(f"✅ Email confirmation message sent successfully to conversation {conv_id}")
        else:
            logging.error(f"❌ Failed to send email confirmation message to conversation {conv_id}")
        
        return jsonify({"status": "success"}), 200
    else:
        if "@" in text:
            logging.warning(f"❌ Email format invalid for: '{text.strip()}' - is_valid_email returned False")
        else:
            logging.info(f"Message does not contain @ symbol: '{text}'")
    
    logging.info(f"Email detection completed for conversation {conv_id}, proceeding with other checks")
    
    # Check if user is confirming email with 'tiim' or 'ugui'
    if text.lower() in ['tiim', 'тийм', 'yes', 'y']:
        # Look for pending email
        pending_email = None
        for msg in history:
            if msg.get("role") == "system" and "pending_email:" in msg.get("content", ""):
                pending_email = msg.get("content").split(":")[1]
                break
        
        if pending_email:
            verification_code = send_verification_email(pending_email)
            if verification_code:
                # Remove pending email and add verification code
                conversation_memory[conv_id] = [msg for msg in conversation_memory[conv_id] 
                                               if not (msg.get("role") == "system" and "pending_email:" in msg.get("content", ""))]
                conversation_memory[conv_id].append({
                    "role": "system", 
                    "content": f"verification_code:{verification_code},email:{pending_email}"
                })
                
                response = "📧 Таны имэйл хаяг руу баталгаажуулах 6 оронтой код илгээлээ. Уг кодыг оруулна уу."
                send_to_chatwoot(conv_id, response)
                return jsonify({"status": "success"}), 200
            else:
                response = "❌ Имэйл илгээхэд алдаа гарлаа. Дахин оролдоно уу эсвэл өөр имэйл хаяг оруулна уу."
                send_to_chatwoot(conv_id, response)
                return jsonify({"status": "success"}), 200
        else:
            response = "⚠️ Баталгаажуулах имэйл хаяг олдсонгүй. Эхлээд имэйл хаягаа оруулна уу."
            send_to_chatwoot(conv_id, response)
            return jsonify({"status": "success"}), 200
    
    # Check if user is rejecting email with 'ugui'
    if text.lower() in ['ugui', 'үгүй', 'no', 'n']:
        # Remove pending email
        if conv_id in conversation_memory:
            conversation_memory[conv_id] = [msg for msg in conversation_memory[conv_id] 
                                           if not (msg.get("role") == "system" and "pending_email:" in msg.get("content", ""))]
        
        response = "❌ Имэйл хаяг буруу байлаа. Зөв имэйл хаягаа дахин оруулна уу."
        send_to_chatwoot(conv_id, response)
        return jsonify({"status": "success"}), 200
    
    # Check if this is a verification code (6 digits)
    if len(text) == 6 and text.isdigit():
        verification_info = None
        for msg in history:
            if msg.get("role") == "system" and "verification_code:" in msg.get("content", ""):
                verification_info = msg.get("content")
                break
        
        if verification_info:
            parts = verification_info.split(",")
            stored_code = parts[0].split(":")[1]
            email = parts[1].split(":")[1]
            
            # Count failed attempts
            failed_attempts = sum(1 for msg in history 
                                if msg.get("role") == "assistant" 
                                and "❌ Баталгаажуулах код буруу байна" in msg.get("content", ""))
            
            if text == stored_code:
                response = "✅ Баталгаажуулалт амжилттай! Одоо асуудлаа дэлгэрэнгүй бичнэ үү."
                send_to_chatwoot(conv_id, response)
                
                conversation_memory[conv_id].append({
                    "role": "system", 
                    "content": f"verified_email:{email}"
                })
                return jsonify({"status": "success"}), 200
            else:
                # Handle failed verification attempts
                if failed_attempts >= 2:  # Allow 3 total attempts (0, 1, 2)
                    response = """❌ Баталгаажуулах кодыг 3 удаа буруу оруулсан тул шинэ код авах шаардлагатай. 
                    
Шинэ код авахын тулд имэйл хаягаа дахин оруулна уу."""
                    send_to_chatwoot(conv_id, response)
                    
                    # Remove old verification code from memory
                    conversation_memory[conv_id] = [msg for msg in conversation_memory[conv_id] 
                                                   if not (msg.get("role") == "system" and "verification_code:" in msg.get("content", ""))]
                    return jsonify({"status": "success"}), 200
                else:
                    remaining_attempts = 2 - failed_attempts
                    response = f"""❌ Баталгаажуулах код буруу байна. 
                    
Танд {remaining_attempts} удаа оролдох боломж үлдлээ. Имэйлээ шалгаж, зөв кодыг оруулна уу."""
                    send_to_chatwoot(conv_id, response)
                    return jsonify({"status": "success"}), 200
        else:
            # No verification code found in memory
            response = """⚠️ Баталгаажуулах код олдсонгүй. 
            
Эхлээд имэйл хаягаа оруулж, баталгаажуулах код авна уу."""
            send_to_chatwoot(conv_id, response)
            return jsonify({"status": "success"}), 200
    
    # Check if user has verified email and is describing an issue
    verified_email = None
    for msg in history:
        if msg.get("role") == "system" and "verified_email:" in msg.get("content", ""):
            verified_email = msg.get("content").split(":")[1]
            break
    
    if verified_email and len(text) > 15:  # User has verified email and writing detailed message
        teams_success = send_to_teams(verified_email, text, conv_id)
        planner_success = create_planner_task(verified_email, text, conv_id)
        
        if teams_success or planner_success:
            # Send confirmation email to user
            confirmation_sent = send_confirmation_email(verified_email, text[:100] + "..." if len(text) > 100 else text)
            
            status_msg = ""
            if teams_success and planner_success:
                status_msg = "✅ Таны асуудлыг хүлээн авлаа."
                
            response = f"{status_msg} Бид тантай удахгүй холбогдох болно. Баярлалаа!"
            
            if confirmation_sent:
                response += "\n📧 Танд баталгаажуулах мэйл илгээлээ."
            
            # Reset session after successful issue forwarding
            conversation_memory[conv_id] = []
            logging.info(f"Session reset for conversation {conv_id} after successful issue forwarding")
            
            send_to_chatwoot(conv_id, response)
            return jsonify({"status": "success"}), 200
    
    # Try to answer with AI first
    ai_response = get_ai_response(text, conv_id, crawled_data)
    
    # Check if AI couldn't find good answer by searching crawled data
    search_results = search_in_crawled_data(text, max_results=3)
    
    # Check if this user was previously escalated but asking a new question
    was_previously_escalated = any(
        msg.get("role") == "system" and "escalated_to_human" in msg.get("content", "")
        for msg in history
    )
    
    # Let AI evaluate its own response quality and decide if human help is needed
    needs_human_help = should_escalate_to_human(text, search_results, ai_response, history)
    
    # If user was previously escalated but AI can answer this new question, respond with AI
    if was_previously_escalated and not needs_human_help:
        # AI can handle this new question even though user was escalated before
        response_with_note = f"{ai_response}\n\n💡 Хэрэв энэ хариулт хангалтгүй бол, имэйл хаягаа оруулж дэмжлэгийн багтай холбогдоно уу."
        send_to_chatwoot(conv_id, response_with_note)
        return jsonify({"status": "success"}), 200
    
    if needs_human_help and not verified_email:
        # Mark this conversation as escalated
        if conv_id not in conversation_memory:
            conversation_memory[conv_id] = []
        conversation_memory[conv_id].append({
            "role": "system", 
            "content": "escalated_to_human"
        })
        
        # AI thinks it can't handle this properly, escalate to human
        escalation_response = """🤝 Би таны асуултад хангалттай хариулт өгч чадахгүй байна. Дэмжлэгийн багийн тусламж авахыг санал болгож байна.

Тусламж авахын тулд имэйл хаягаа оруулна уу."""
        
        send_to_chatwoot(conv_id, escalation_response)
    else:
        # AI is confident in its response, send it
        send_to_chatwoot(conv_id, ai_response)

    return jsonify({"status": "success"}), 200


def should_escalate_to_human(user_message: str, search_results: list, ai_response: str, history: list) -> bool:
    """AI evaluates its own response and decides if human help is needed"""
    
    # Use AI to evaluate its own response quality
    if not client:
        # Fallback without AI evaluation - be more lenient
        return len(user_message) > 50 and (not search_results or len(search_results) == 0)
    
    # Build context for AI self-evaluation
    context = f"""Хэрэглэгчийн асуулт: "{user_message}"

Манай баримт бичгээс хайсан үр дүн:
{f"Олдсон: {len(search_results)} үр дүн" if search_results else "Мэдээлэл олдсонгүй"}

Миний өгсөн хариулт: "{ai_response}"

Ярилцлагын сүүлийн мессежүүд:"""
    
    if history:
        recent_messages = [msg.get("content", "")[:100] for msg in history[-3:] if msg.get("role") == "user"]
        if recent_messages:
            context += "\n" + "\n".join(recent_messages)
    
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": """Та өөрийн өгсөн хариултыг үнэлж, хэрэглэгчид хангалттай эсэхийг шийднэ.

Дараах тохиолдлуудад л хүний ажилтны тусламж шаардлагатай:
- Хэрэглэгч техникийн алдаа, тохиргооны асуудлаар тусламж хүсэж байгаа
- Акаунт, төлбөр, хостинг, домэйн зэрэг Cloud.mn-ийн үйлчилгээтэй холбоотой асуудал
- Тусгай хүсэлт, гомдол, шуурхай тусламж хэрэгтэй асуудал
- Хэрэглэгч өөрөө "ажилтныг хүсэж байна" гэж тодорхой хэлсэн тохиолдол
- Миний хариулт нь хэрэглэгчийн асуултын үндсэн сэдвээс огт холдсон бол

Дараах тохиолдлуудад хүний тусламж ШААРДЛАГАГҮЙ:
- Энгийн мэдээлэл асуух (Cloud.mn docs-ийн тухай)
- Ерөнхий зөвлөгөө авах
- Техникийн мэдлэг судлах
- Би хангалттай хариулт өгч чадсан тохиолдол
- Хэрэглэгч зүгээр л мэдээлэл хайж байгаа

Өөрийнхөө хариултанд итгэлтэй байж, хэрэглэгч дахин асууж болно гэдгийг санаарай.

Хариултаа зөвхөн 'YES' (хүний тусламж хэрэгтэй) эсвэл 'NO' (миний хариулт хангалттай) гэж өгнө үү."""
                },
                {
                    "role": "user", 
                    "content": context
                }
            ],
            max_tokens=10,
            temperature=0.2
        )
        
        ai_decision = (response.choices[0].message.content or "NO").strip().upper()
        logging.info(f"AI self-evaluation for '{user_message[:30]}...': {ai_decision}")
        return ai_decision == "YES"
        
    except Exception as e:
        logging.error(f"AI self-evaluation error: {e}")
        # More lenient fallback - don't escalate by default
        return False


# —— Additional API Endpoints —— #
@app.route("/api/crawl-status", methods=["GET"])
def get_crawl_status():
    """Get current crawl status"""
    return jsonify({
        "crawl_status": crawl_status,
        "crawled_pages": len(crawled_data),
        "config": {
            "root_url": ROOT_URL,
            "auto_crawl_enabled": AUTO_CRAWL_ON_START,
            "max_pages": MAX_CRAWL_PAGES
        }
    })

@app.route("/api/force-crawl", methods=["POST"])
def force_crawl():
    """Force start a new crawl"""
    global crawled_data, crawl_status
    
    # Check if already running
    if crawl_status["status"] == "running":
        return jsonify({"error": "Crawl is already running"}), 409
    
    try:
        crawl_status = {"status": "running", "message": "Force crawl started via API"}
        crawled_data = crawl_and_scrape(ROOT_URL)
        
        if crawled_data:
            crawl_status = {
                "status": "completed",
                "message": f"Force crawl completed via API",
                "pages_count": len(crawled_data),
                "timestamp": datetime.now().isoformat()
            }
            return jsonify({
                "status": "success",
                "pages_crawled": len(crawled_data),
                "crawl_status": crawl_status
            })
        else:
            crawl_status = {"status": "failed", "message": "Force crawl failed - no pages found"}
            return jsonify({"error": "No pages were crawled"}), 500
            
    except Exception as e:
        crawl_status = {"status": "error", "message": f"Force crawl error: {str(e)}"}
        return jsonify({"error": f"Crawl failed: {e}"}), 500

@app.route("/api/search", methods=["POST"])
def api_search():
    """Search through crawled data via API"""
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    max_results = data.get("max_results", 5)
    
    if not query:
        return jsonify({"error": "Missing 'query' in request body"}), 400
    
    if crawl_status["status"] == "running":
        return jsonify({"error": "Crawl is currently running, please wait"}), 409
    
    if not crawled_data:
        return jsonify({"error": "No crawled data available. Run crawl first."}), 404
    
    results = search_in_crawled_data(query, max_results)
    return jsonify({
        "query": query,
        "results_count": len(results),
        "results": results,
        "crawl_status": crawl_status
    })

@app.route("/api/conversation/<int:conv_id>/memory", methods=["GET"])
def get_conversation_memory(conv_id):
    """Get conversation memory for debugging"""
    memory = conversation_memory.get(conv_id, [])
    return jsonify({"conversation_id": conv_id, "memory": memory})

@app.route("/api/conversation/<int:conv_id>/clear", methods=["POST"])
def clear_conversation_memory(conv_id):
    """Clear conversation memory"""
    if conv_id in conversation_memory:
        del conversation_memory[conv_id]
    return jsonify({"status": "cleared", "conversation_id": conv_id})

@app.route("/api/crawled-data", methods=["GET"])
def get_crawled_data():
    """Get current crawled data"""
    page_limit = request.args.get('limit', 10, type=int)
    return jsonify({
        "total_pages": len(crawled_data), 
        "crawl_status": crawl_status,
        "data": crawled_data[:page_limit]
    })

@app.route("/api/planner/create-task", methods=["POST"])
def api_create_planner_task():
    """Manual-аар Microsoft Planner-д task үүсгэх API"""
    data = request.get_json(force=True)
    email = data.get("email", "").strip()
    issue = data.get("issue", "").strip()
    conv_id = data.get("conversation_id")
    
    if not email or not issue:
        return jsonify({"error": "Имэйл болон асуудал заавал шаардлагатай"}), 400
    
    if not is_valid_email(email):
        return jsonify({"error": "Имэйл хаягийн формат буруу байна"}), 400
    
    success = create_planner_task(email, issue, conv_id)
    
    if success:
        return jsonify({
            "status": "success",
            "message": "Microsoft Planner-д task амжилттай үүсгэлээ",
            "email": email,
            "issue_preview": issue[:100] + "..." if len(issue) > 100 else issue
        })
    else:
        return jsonify({
            "status": "error", 
            "message": "Microsoft Planner-д task үүсгэх амжилтгүй боллоо"
        }), 500

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    chatwoot_test = test_chatwoot_api()
    
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "crawl_status": crawl_status,
        "crawled_pages": len(crawled_data),
        "active_conversations": len(conversation_memory),
        "chatwoot_api_test": chatwoot_test,
        "config": {
            "root_url": ROOT_URL,
            "auto_crawl_enabled": AUTO_CRAWL_ON_START,
            "openai_configured": client is not None,
            "chatwoot_configured": bool(CHATWOOT_API_KEY and ACCOUNT_ID),
            "chatwoot_account_id": ACCOUNT_ID,
            "teams_configured": bool(TEAMS_WEBHOOK_URL),
            "teams_webhook_url": TEAMS_WEBHOOK_URL,
            "planner_tenant_id": PLANNER_TENANT_ID,
            "planner_client_id": PLANNER_CLIENT_ID,
            "planner_client_secret": PLANNER_CLIENT_SECRET,
            "planner_plan_id": PLANNER_PLAN_ID,
            "planner_bucket_id": PLANNER_BUCKET_ID,
            "planner_configured": bool(PLANNER_TENANT_ID and PLANNER_CLIENT_ID and PLANNER_CLIENT_SECRET and PLANNER_PLAN_ID and PLANNER_BUCKET_ID),
            "smtp_configured": bool(SMTP_SERVER and SMTP_USERNAME and SMTP_PASSWORD)
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
# —— Email Verification Functions —— #
def is_valid_email(email: str) -> bool:
    """Check if email format is valid"""
    # More flexible email regex that handles most common cases
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    # Clean the email first
    cleaned_email = email.strip().lower()
    
    # Basic checks
    if not cleaned_email or cleaned_email.count('@') != 1:
        logging.info(f"Email validation for '{email}': False (basic check failed)")
        return False
    
    # Check with regex
    is_valid = bool(re.match(email_regex, cleaned_email))
    logging.info(f"Email validation for '{email}' (cleaned: '{cleaned_email}'): {is_valid}")
    
    # Additional simple check for common patterns
    if not is_valid:
        # Try a simpler pattern for debugging
        simple_pattern = r'^[^@\s]+@[^@\s]+\.[^@\s]+$'
        simple_valid = bool(re.match(simple_pattern, cleaned_email))
        logging.info(f"Simple email pattern check for '{cleaned_email}': {simple_valid}")
        
        # For debugging, let's be more lenient
        if simple_valid:
            logging.info(f"Accepting email '{cleaned_email}' with simple pattern")
            return True
    
    return is_valid

def send_verification_email(email: str) -> Optional[str]:
    """Send verification email with code and return the code"""
    if not SMTP_FROM_EMAIL or not SMTP_PASSWORD or not SMTP_SERVER:
        logging.error("SMTP credentials not configured")
        return None
        
    # Generate verification code
    verification_code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
    
    # Create email
    msg = MIMEMultipart()
    msg['From'] = SMTP_FROM_EMAIL
    msg['To'] = email
    msg['Subject'] = "Cloud.mn баталгаажуулах код"
    
    body = f"""Сайн байна уу,

Таны Cloud.mn-д хандсан хүсэлтийг баталгаажуулахын тулд доорх кодыг оруулна уу:

{verification_code}

Хэрэв та энэ хүсэлтийг илгээгээгүй бол мэдэгдэнэ үү.

Хүндэтгэсэн,
Cloud.mn тусламжийн үйлчилгээ"""
    
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME or "", SMTP_PASSWORD or "")
        server.send_message(msg)
        server.quit()
        logging.info(f"Verification email sent to {email}")
        return verification_code
    except Exception as e:
        logging.error(f"Failed to send verification email: {e}")
        return None

def send_confirmation_email(email: str, problem: str) -> bool:
    """Send confirmation email after issue is sent to support team"""
    if not SMTP_FROM_EMAIL or not SMTP_PASSWORD or not SMTP_SERVER:
        logging.error("SMTP credentials not configured")
        return False
        
    # Create email
    msg = MIMEMultipart()
    msg['From'] = SMTP_FROM_EMAIL
    msg['To'] = email
    msg['Subject'] = "Cloud.mn - Таны хүсэлтийг хүлээн авлаа"
    
    body = f"""Сайн байна уу,

Таны "{problem}" асуудлыг тусламжийн баг руу амжилттай илгээлээ.

Бид таны хүсэлтийг хүлээн авч, удахгүй танд хариу өгөх болно.

Хүндэтгэсэн,
Cloud.mn тусламжийн үйлчилгээ"""
    
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME or "", SMTP_PASSWORD or "")
        server.send_message(msg)
        server.quit()
        logging.info(f"Confirmation email sent to {email}")
        return True
    except Exception as e:
        logging.error(f"Failed to send confirmation email: {e}")
        return False

def test_chatwoot_api():
    """Test Chatwoot API connectivity"""
    if not CHATWOOT_API_KEY or not ACCOUNT_ID:
        return {"status": "error", "message": "Chatwoot credentials not configured"}
    
    try:
        # Test API connectivity by getting account info
        api_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}"
        headers = {"api_access_token": CHATWOOT_API_KEY}
        
        response = requests.get(api_url, headers=headers, timeout=10)
        if response.status_code == 200:
            account_data = response.json()
            return {
                "status": "success",
                "account_name": account_data.get("name", "Unknown"),
                "account_id": ACCOUNT_ID,
                "api_base": CHATWOOT_BASE_URL
            }
        else:
            return {
                "status": "error", 
                "message": f"API returned {response.status_code}: {response.text[:200]}"
            }
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}
