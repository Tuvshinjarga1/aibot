import os
import time
import logging
import requests
from openai import OpenAI
import json
from urllib.parse import urljoin, urlparse
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup
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
CHATWOOT_BASE_URL    = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com")
# CHATWOOT_BASE_URL    = os.getenv("CHATWOOT_BASE_URL", "https://chat.cloud.mn")
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

def get_planner_access_token() -> str:
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
    
        if assigned_user_id:
            data["assignments"] = {
                assigned_user_id: {
                    "@odata.type": "#microsoft.graph.plannerAssignment",
                    "orderHint": " !"
                }
            }

        try:
            response = requests.post(url, headers=self.headers, json=data, timeout=10)
            return response.json()
        except Exception as e:
            logging.error(f"Planner task үүсгэхэд алдаа гарлаа: {e}")
            return {"error": str(e)}

def create_planner_task(email: str, issue: str, conv_id: int = None) -> bool:
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
        
        # Task үүсгэх
        result = planner.create_task(
            plan_id=PLANNER_PLAN_ID,
            bucket_id=PLANNER_BUCKET_ID,
            title=title,
            priority=1  # Өндөр ач холбогдол
        )
        
        if "error" not in result and result.get("id"):
            task_id = result.get("id")
            logging.info(f"Microsoft Planner task амжилттай үүсгэлээ: {task_id} - {email}")
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
        title = soup.title.string.strip() if soup.title else url
        body, images = extract_content(soup, url)

        results.append({
            "url": url,
            "title": title,
            "body": body,
            "images": images
        })

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if is_internal_link(href):
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
    main = soup.find("main") or soup
    texts = []
    images = []

    for tag in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "code"]):
        text = tag.get_text(strip=True)
        if text:
            texts.append(text)

    for img in main.find_all("img"):
        src = img.get("src")
        alt = img.get("alt", "").strip()
        if src:
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
    title = soup.title.string.strip() if soup.title else url
    body, images = extract_content(soup, url)
    return {"url": url, "title": title, "body": body, "images": images}


# —— AI Assistant Functions —— #
def get_ai_response(user_message: str, conversation_id: int, context_data: list = None):
    """Enhanced AI response with better context awareness"""
    
    if not client:
        return "🔑 OpenAI API түлхүүр тохируулагдаагүй байна. Админтай холбогдоно уу."
    
    # Get conversation history
    history = conversation_memory.get(conversation_id, [])
    
    # Build context from crawled data if available
    context = ""
    if context_data and crawled_data:
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
            model="gpt-4.1",
            messages=messages,
            max_tokens=500,  # Increased token limit for better responses
            temperature=0.7
        )
        
        ai_response = response.choices[0].message.content
        
        # Store in memory
        if conversation_id not in conversation_memory:
            conversation_memory[conversation_id] = []
        
        conversation_memory[conversation_id].append({"role": "user", "content": user_message})
        conversation_memory[conversation_id].append({"role": "assistant", "content": ai_response})
        
        # Keep only last 8 messages
        if len(conversation_memory[conversation_id]) > 8:
            conversation_memory[conversation_id] = conversation_memory[conversation_id][-8:]
            
        return ai_response
        
    except Exception as e:
        logging.error(f"OpenAI API алдаа: {e}")
        return f"🔧 AI-тай холбогдоход саад гарлаа. Дараах зүйлсийг туршиж үзнэ үү:\n• Асуултаа дахин илгээнэ үү\n• Асуултаа тодорхой болгоно уу\n• Холбогдох мэдээллийг хайж үзнэ үү\n\nАлдааны дэлгэрэнгүй: {str(e)[:100]}"

def search_in_crawled_data(query: str, max_results: int = 3):
    """Enhanced search through crawled data with better relevance scoring"""
    if not crawled_data:
        return []
    
    query_lower = query.lower()
    results = []
    scored_pages = []
    
    for page in crawled_data:
        score = 0
        title = page['title'].lower()
        body = page['body'].lower()
        
        # Title matches are more important
        if query_lower in title:
            score += 3
        elif any(word in title for word in query_lower.split()):
            score += 2
            
        # Body matches
        if query_lower in body:
            score += 2
        elif any(word in body for word in query_lower.split()):
            score += 1
            
        # Exact phrase matches are very important
        if f'"{query_lower}"' in body:
            score += 4
            
        if score > 0:
            scored_pages.append((score, page))
    
    # Sort by score and get top results
    scored_pages.sort(key=lambda x: x[0], reverse=True)  # Sort by score (first element of tuple)
    for score, page in scored_pages[:max_results]:
        # Find the most relevant snippet
        body = page['body']
        query_words = query_lower.split()
        
        # Try to find a good context around the query
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
            'snippet': best_snippet,
            'relevance_score': score
        })
            
    return results

def scrape_single(url: str):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title else url
    body, images = extract_content(soup, url)
    return {"url": url, "title": title, "body": body, "images": images}


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
def send_to_teams(email: str, issue: str, conv_id: int = None) -> bool:
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
    """Enhanced webhook with AI integration"""
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
    
    # Get conversation history
    history = conversation_memory.get(conv_id, [])
    
    # Check if this is an email address
    if "@" in text and is_valid_email(text.strip()):
        verification_code = send_verification_email(text.strip())
        if verification_code:
            if conv_id not in conversation_memory:
                conversation_memory[conv_id] = []
            conversation_memory[conv_id].append({
                "role": "system", 
                "content": f"verification_code:{verification_code},email:{text.strip()}"
            })
            
            response = "📧 Таны имэйл хаяг руу баталгаажуулах код илгээлээ. Уг кодыг оруулна уу."
            send_to_chatwoot(conv_id, response)
            return jsonify({"status": "success"}), 200
        else:
            response = "❌ Имэйл илгээхэд алдаа гарлаа. Дахин оролдоно уу эсвэл өөр имэйл хаяг оруулна уу."
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
            
            if text == stored_code:
                response = "✅ Баталгаажуулалт амжилттай. Одоо асуудлаа дэлгэрэнгүй бичнэ үү."
                send_to_chatwoot(conv_id, response)
                
                conversation_memory[conv_id].append({
                    "role": "system", 
                    "content": f"verified_email:{email}"
                })
                return jsonify({"status": "success"}), 200
            else:
                response = "❌ Баталгаажуулах код буруу байна. Дахин оролдоно уу."
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
                status_msg = "✅ Таны асуудлыг Teams болон Planner-д амжилттай илгээлээ."
            elif teams_success:
                status_msg = "✅ Таны асуудлыг Teams-д амжилттай илгээлээ."
            elif planner_success:
                status_msg = "✅ Таны асуудлыг Planner-д амжилттай илгээлээ."
            else:
                status_msg = "⚠️ Таны асуудлыг хүлээн авлаа."
                
            response = f"{status_msg} Бид тантай удахгүй холбогдох болно. Баярлалаа!"
            
            if confirmation_sent:
                response += "\n📧 Танд баталгаажуулах мэйл илгээлээ."
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

Тусламж авахын тулд имэйл хаягаа оруулна уу. Бид таны имэйл хаягийг баталгаажуулсны дараа асуудлыг шийдвэрлэх болно."""
        
        send_to_chatwoot(conv_id, escalation_response)
    else:
        # AI is confident in its response, send it
        send_to_chatwoot(conv_id, ai_response)

    return jsonify({"status": "success"}), 200


def should_escalate_to_human(user_message: str, search_results: list, ai_response: str, history: list) -> bool:
    """AI evaluates its own response and decides if human help is needed"""
    
    # Use AI to evaluate its own response quality
    if not client:
        # Fallback without AI evaluation
        return not search_results or (search_results and search_results[0].get('relevance_score', 0) < 2)
    
    # Build context for AI self-evaluation
    context = f"""Хэрэглэгчийн асуулт: "{user_message}"

Манай баримт бичгээс хайсан үр дүн:
{f"Олдсон: {len(search_results)} үр дүн, хамгийн сайн оноо: {search_results[0].get('relevance_score', 0)}" if search_results else "Мэдээлэл олдсонгүй"}

Миний өгсөн хариулт: "{ai_response}"

Ярилцлагын сүүлийн мессежүүд:"""
    
    if history:
        recent_messages = [msg.get("content", "")[:100] for msg in history[-2:] if msg.get("role") == "user"]
        if recent_messages:
            context += "\n" + "\n".join(recent_messages)
    
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": """Та өөрийн өгсөн хариултыг үнэлж, хэрэглэгчид хангалттай эсэхийг шийднэ.

Дараах тохиолдлуудад хүний тусламж шаардлагатай:
- Хэрэглэгчийн асуултад тодорхой хариулт өгч чадаагүй
- Баримт бичгээс хангалттай мэдээлэл олдоогүй  
- Техникийн алдаа, тохиргоо, акаунтын асуудал
- Cloud.mn-ээс өөр үйлчилгээ хүсэж байгаа (хостинг, домэйн гэх мэт)
- Тусгай хүсэлт, гомдол, санал
- Хариулт ерөнхий, тодорхойгүй байгаа

Хэрэв хэрэглэгчийн асуултад хангалттай, тодорхой хариулт өгсөн бол 'NO'.
Хэрэв хариулт хангалтгүй эсвэл хүний тусламж хэрэгтэй бол 'YES'.

Хариултаа зөвхөн 'YES' эсвэл 'NO' гэж өгнө үү."""
                },
                {
                    "role": "user", 
                    "content": context
                }
            ],
            max_tokens=10,
            temperature=0.1
        )
        
        ai_decision = response.choices[0].message.content.strip().upper()
        logging.info(f"AI self-evaluation for conv {user_message[:50]}...: {ai_decision}")
        return ai_decision == "YES"
        
    except Exception as e:
        logging.error(f"AI self-evaluation error: {e}")
        # Fallback: escalate if no good search results
        return not search_results or (search_results and search_results[0].get('relevance_score', 0) < 2)


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
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "crawl_status": crawl_status,
        "crawled_pages": len(crawled_data),
        "active_conversations": len(conversation_memory),
        "config": {
            "root_url": ROOT_URL,
            "auto_crawl_enabled": AUTO_CRAWL_ON_START,
            "openai_configured": client is not None,
            "chatwoot_configured": bool(CHATWOOT_API_KEY and ACCOUNT_ID),
            "teams_configured": bool(TEAMS_WEBHOOK_URL),
            "planner_configured": bool(PLANNER_TENANT_ID and PLANNER_CLIENT_ID and PLANNER_CLIENT_SECRET and PLANNER_PLAN_ID and PLANNER_BUCKET_ID),
            "smtp_configured": bool(SMTP_SERVER and SMTP_USERNAME and SMTP_PASSWORD)
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
# —— Email Verification Functions —— #
def is_valid_email(email: str) -> bool:
    """Check if email format is valid"""
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_regex, email))

def send_verification_email(email: str) -> str:
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
Cloud.mn Баг"""
    
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
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

Таны "{problem}" асуудлыг дэмжлэгийн багт амжилттай илгээлээ.

Бид таны хүсэлтийг хүлээн авч, удахгүй танд хариу өгөх болно. Та нэмэлт мэдээлэл шаардлагатай бол манай багтай холбогдоно уу.

Хүндэтгэсэн,
Cloud.mn Дэмжлэгийн Баг"""
    
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        logging.info(f"Confirmation email sent to {email}")
        return True
    except Exception as e:
        logging.error(f"Failed to send confirmation email: {e}")
        return False
