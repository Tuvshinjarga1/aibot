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
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY")
AUTO_CRAWL_ON_START  = os.getenv("AUTO_CRAWL_ON_START", "true").lower() == "true"

# Microsoft Teams webhook config
TEAMS_WEBHOOK_URL    = os.getenv("TEAMS_WEBHOOK_URL")
ENABLE_TEAMS_FALLBACK = os.getenv("ENABLE_TEAMS_FALLBACK", "true").lower() == "true"

# SMTP configuration for email verification
SMTP_SERVER          = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT            = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME        = os.getenv("SENDER_EMAIL")
SMTP_PASSWORD        = os.getenv("SENDER_PASSWORD")
SMTP_FROM_EMAIL      = os.getenv("SENDER_EMAIL")
ENABLE_EMAIL_VERIFICATION = os.getenv("ENABLE_EMAIL_VERIFICATION", "true").lower() == "true"

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# —— Memory Storage —— #
conversation_memory = {}
crawled_data = []
crawl_status = {"status": "not_started", "message": "Crawling has not started yet"}

# Email verification and support request storage
email_verification_codes = {}  # {conv_id: {"email": email, "code": code, "timestamp": timestamp}}
pending_support_requests = {}  # {conv_id: {"email": email, "question": question, "details": details, "verified": bool}}

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
        return {
            "response": "🔑 OpenAI API түлхүүр тохируулагдаагүй байна. Админтай холбогдоно уу.", 
            "needs_human": True,
            "human_requested": False
        }
    
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
    5. Хэрэв хэрэглэгч хүний туслалцаа хүссэн бол (дэмжлэгийн баг, хүмүүс, туслалцаа гэх мэт) шууд "HUMAN_REQUESTED:" гэж эхлээрэй
    
    ЧУХАЛ: 
    - Зөвхөн техникийн асуулт, нарийн төвөгтэй асуулт эсвэл та мэдэхгүй зүйлийн хувьд "NEEDS_HUMAN:" ашиглана уу
    - Хэрэглэгч хүний туслалцаа шууд хүссэн бол "HUMAN_REQUESTED:" ашиглана уу
    - Энгийн асуултад хэвийн хариулна уу
    
    Жишээ:
    - "Дэмжлэгийн багтай холбогдмоор байна" → "HUMAN_REQUESTED: Танд дэмжлэгийн багтай холбогдох боломжийг олгож байна..."
    - "Project owner солиулмаар байна" → "HUMAN_REQUESTED: Танд дэмжлэгийн багтай холбогдох боломжийг олгож байна..."
    - "Docker-ийн тохиргоо яаж хийх вэ?" → Хэвийн хариулт өгнө
    - "Энэ алдааны шийдлийг мэдэхгүй байна" → "NEEDS_HUMAN: Энэ асуултын талаар..."
    
    Боломжит командууд:
    - crawl: Бүх сайтыг шүүрдэх
    - scrape <URL>: Тодорхой хуудсыг шүүрдэх  
    - help: Тусламж харуулах
    - search <асуулт>: Мэдээлэл хайх"""
    
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
        
        # Check if AI indicates it needs human help or user requested human
        needs_human = ai_response.startswith("NEEDS_HUMAN:")
        human_requested = ai_response.startswith("HUMAN_REQUESTED:")
        
        if needs_human:
            # Remove the NEEDS_HUMAN: prefix from the response
            ai_response = ai_response.replace("NEEDS_HUMAN:", "").strip()
        elif human_requested:
            # Remove the HUMAN_REQUESTED: prefix from the response
            ai_response = ai_response.replace("HUMAN_REQUESTED:", "").strip()
        
        # Store in memory
        if conversation_id not in conversation_memory:
            conversation_memory[conversation_id] = []
        
        conversation_memory[conversation_id].append({"role": "user", "content": user_message})
        conversation_memory[conversation_id].append({"role": "assistant", "content": ai_response})
        
        # Keep only last 8 messages
        if len(conversation_memory[conversation_id]) > 8:
            conversation_memory[conversation_id] = conversation_memory[conversation_id][-8:]
            
        return {
            "response": ai_response, 
            "needs_human": needs_human or human_requested,
            "human_requested": human_requested
        }
        
    except Exception as e:
        logging.error(f"OpenAI API алдаа: {e}")
        error_response = f"🔧 AI-тай холбогдоход саад гарлаа. Та дараах аргуудаар тусламж авч болно:\n• 'help' командыг ашиглана уу\n• 'crawl' эсвэл 'search' командуудыг туршина уу\n\nАлдааны дэлгэрэнгүй: {str(e)[:100]}"
        return {
            "response": error_response, 
            "needs_human": True,
            "human_requested": False
        }

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

def send_to_teams(user_email: str, user_name: str, question: str, conversation_id: int, conversation_url: str = None):
    """Send notification to Microsoft Teams when AI cannot answer"""
    if not TEAMS_WEBHOOK_URL or not ENABLE_TEAMS_FALLBACK:
        logging.warning("Teams webhook not configured or disabled")
        return False
    
    # Create conversation URL if not provided
    if not conversation_url:
        conversation_url = f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conversation_id}"
    
    # Create Teams adaptive card
    teams_payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.cards.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.3",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "🚨 AI Туслах Хариулж Чадсангүй",
                            "weight": "Bolder",
                            "size": "Large",
                            "color": "Attention"
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {
                                    "title": "👤 Хэрэглэгч:",
                                    "value": f"{user_name} ({user_email})"
                                },
                                {
                                    "title": "💬 Харилцаа ID:",
                                    "value": str(conversation_id)
                                },
                                {
                                    "title": "⏰ Цаг:",
                                    "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                }
                            ]
                        },
                        {
                            "type": "TextBlock",
                            "text": "❓ **Асуулт:**",
                            "weight": "Bolder",
                            "size": "Medium"
                        },
                        {
                            "type": "TextBlock",
                            "text": question,
                            "wrap": True,
                            "style": "emphasis"
                        },
                        {
                            "type": "TextBlock",
                            "text": "⚠️ **Шалтгаан:** AI систем энэ асуултад хариулж чадсангүй эсвэл хангалттай мэдээлэл олдсонгүй.",
                            "wrap": True,
                            "color": "Warning"
                        }
                    ],
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "💬 Харилцаа нээх",
                            "url": conversation_url
                        },
                        {
                            "type": "Action.OpenUrl", 
                            "title": "📧 Хэрэглэгчтэй холбогдох",
                            "url": f"mailto:{user_email}?subject=Таны асуултын талаар&body=Сайн байна уу {user_name},%0A%0AТаны асуулт: {question}%0A%0A"
                        }
                    ]
                }
            }
        ]
    }
    
    try:
        response = requests.post(TEAMS_WEBHOOK_URL, json=teams_payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Successfully sent Teams notification for conversation {conversation_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to send Teams notification: {e}")
        return False

def generate_verification_code():
    """Generate 6-digit verification code"""
    return ''.join(random.choices(string.digits, k=6))

def send_verification_email(email: str, code: str, user_name: str = "Хэрэглэгч"):
    """Send email verification code via SMTP"""
    if not all([SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL]):
        logging.error("SMTP configuration incomplete")
        return False
    
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = SMTP_FROM_EMAIL
        msg['To'] = email
        msg['Subject'] = "Cloud.mn - Email баталгаажуулах код"
        
        # Email body
        body = f"""
Сайн байна уу {user_name}!

Таны Cloud.mn дэмжлэгийн хүсэлтийг баталгаажуулахын тулд доорх кодыг оруулна уу:

🔐 Баталгаажуулах код: {code}

Энэ код 10 минутын дараа хүчингүй болно.

Хэрэв та энэ хүсэлтийг илгээгээгүй бол Cloud mn рүү мэдэгдэнэ үү..

Баярлалаа,
Cloud.mn баг
        """
        
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # Send email
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(SMTP_FROM_EMAIL, email, text)
        server.quit()
        
        logging.info(f"Verification email sent to {email}")
        return True
        
    except Exception as e:
        logging.error(f"Failed to send verification email: {e}")
        return False

def start_email_verification(conv_id: int, email: str, user_name: str, original_question: str):
    """Start email verification process"""
    global email_verification_codes, pending_support_requests
    
    # Generate verification code
    code = generate_verification_code()
    timestamp = datetime.now()
    
    # Store verification data
    email_verification_codes[conv_id] = {
        "email": email,
        "code": code,
        "timestamp": timestamp,
        "user_name": user_name
    }
    
    # Store pending support request
    pending_support_requests[conv_id] = {
        "email": email,
        "question": original_question,
        "details": "",
        "verified": False,
        "user_name": user_name
    }
    
    # Send verification email
    if send_verification_email(email, code, user_name):
        return True
    else:
        # Clean up if email failed
        if conv_id in email_verification_codes:
            del email_verification_codes[conv_id]
        if conv_id in pending_support_requests:
            del pending_support_requests[conv_id]
        return False

def verify_email_code(conv_id: int, submitted_code: str):
    """Verify submitted email code"""
    global email_verification_codes, pending_support_requests
    
    if conv_id not in email_verification_codes:
        return {"success": False, "message": "Баталгаажуулах код олдсонгүй. Дахин эхлүүлнэ үү."}
    
    verification_data = email_verification_codes[conv_id]
    
    # Check if code expired (10 minutes)
    time_diff = datetime.now() - verification_data["timestamp"]
    if time_diff.total_seconds() > 600:  # 10 minutes
        del email_verification_codes[conv_id]
        if conv_id in pending_support_requests:
            del pending_support_requests[conv_id]
        return {"success": False, "message": "Баталгаажуулах код хугацаа дууссан. Дахин эхлүүлнэ үү."}
    
    # Check code
    if submitted_code.strip() == verification_data["code"]:
        # Mark as verified
        if conv_id in pending_support_requests:
            pending_support_requests[conv_id]["verified"] = True
        
        # Clean up verification code
        del email_verification_codes[conv_id]
        
        return {"success": True, "message": "Email амжилттай баталгаажлаа!"}
    else:
        return {"success": False, "message": "Буруу код байна. Дахин оролдоно уу."}

def collect_support_details(conv_id: int, details: str):
    """Collect additional details for support request"""
    global pending_support_requests
    
    if conv_id not in pending_support_requests:
        return {"success": False, "message": "Дэмжлэгийн хүсэлт олдсонгүй."}
    
    if not pending_support_requests[conv_id]["verified"]:
        return {"success": False, "message": "Эхлээд email хаягаа баталгаажуулна уу."}
    
    # Store details
    pending_support_requests[conv_id]["details"] = details
    
    return {"success": True, "message": "Дэлгэрэнгүй мэдээлэл хадгалагдлаа!"}

def finalize_support_request(conv_id: int):
    """Send final support request to Teams"""
    global pending_support_requests
    
    if conv_id not in pending_support_requests:
        return {"success": False, "message": "Дэмжлэгийн хүсэлт олдсонгүй."}
    
    request_data = pending_support_requests[conv_id]
    
    if not request_data["verified"]:
        return {"success": False, "message": "Email баталгаажуулаагүй байна."}
    
    # Send to Teams
    success = send_to_teams(
        user_email=request_data["email"],
        user_name=request_data["user_name"],
        question=f"{request_data['question']}\n\nДэлгэрэнгүй: {request_data['details']}",
        conversation_id=conv_id
    )
    
    if success:
        # Clean up
        del pending_support_requests[conv_id]
        return {"success": True, "message": "Дэмжлэгийн хүсэлт амжилттай илгээгдлээ!"}
    else:
        return {"success": False, "message": "Teams-рүү илгээхэд алдаа гарлаа."}


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
    """Enhanced webhook with AI integration, email verification and Teams fallback"""
    global crawled_data, crawl_status, email_verification_codes, pending_support_requests
    
    data = request.json or {}
    
    # Only process incoming messages
    if data.get("message_type") != "incoming":
        return jsonify({}), 200

    conv_id = data["conversation"]["id"]
    text = data.get("content", "").strip()
    contact = data.get("conversation", {}).get("contact", {})
    contact_name = contact.get("name", "Хэрэглэгч")
    contact_email = contact.get("email", "")
    
    logging.info(f"Received message from {contact_name} ({contact_email}) in conversation {conv_id}: {text}")
    
    # Check if user is in email verification process
    if conv_id in email_verification_codes:
        # User is submitting verification code
        result = verify_email_code(conv_id, text)
        send_to_chatwoot(conv_id, result["message"])
        
        if result["success"]:
            # Ask for more details
            send_to_chatwoot(conv_id, 
                "✅ Email баталгаажлаа! Одоо асуудлынхаа талаар дэлгэрэнгүй мэдээлэл өгнө үү. "
                "Жишээ нь: ямар алдаа гарч байна, хэзээнээс эхэлсэн, юу хийх гэж байсан гэх мэт."
            )
        
        return jsonify({"status": "success"}), 200
    
    # Check if user is providing support details
    if conv_id in pending_support_requests and pending_support_requests[conv_id]["verified"] and not pending_support_requests[conv_id]["details"]:
        # User is providing additional details
        result = collect_support_details(conv_id, text)
        send_to_chatwoot(conv_id, result["message"])
        
        if result["success"]:
            # Finalize and send to Teams
            final_result = finalize_support_request(conv_id)
            send_to_chatwoot(conv_id, final_result["message"])
            
            if final_result["success"]:
                send_to_chatwoot(conv_id, 
                    "🎯 Таны дэмжлэгийн хүсэлт баталгаажсан email хаяг болон дэлгэрэнгүй "
                    "мэдээллийн хамт манай багт илгээгдлээ. Тэд удахгүй танд хариулах болно."
                )
        
        return jsonify({"status": "success"}), 200
    
    # Regular AI conversation
    ai_result = get_ai_response(text, conv_id, crawled_data)
    ai_response = ai_result["response"]
    needs_human = ai_result["needs_human"]
    human_requested = ai_result.get("human_requested", False)
    
    # Send AI response to chatwoot
    send_to_chatwoot(conv_id, ai_response)
    
    # If AI needs human help, start email verification process
    if needs_human and ENABLE_TEAMS_FALLBACK and ENABLE_EMAIL_VERIFICATION:
        logging.info(f"Starting email verification process for conversation {conv_id}")
        
        if not contact_email:
            send_to_chatwoot(conv_id, 
                "⚠️ Дэмжлэгийн багт хүсэлт илгээхийн тулд та эхлээд email хаяг баталгаажуулна уу."
            )
            return jsonify({"status": "success"}), 200
        
        # Start email verification
        if start_email_verification(conv_id, contact_email, contact_name, text):
            verification_message = (
                f"📧 Таны email хаяг ({contact_email}) руу баталгаажуулах код илгээлээ. "
                f"Кодыг энд оруулж дэмжлэгийн хүсэлтээ баталгаажуулна уу.\n\n"
                f"💡 Код 10 минутын дараа хүчингүй болно."
            )
            send_to_chatwoot(conv_id, verification_message)
        else:
            send_to_chatwoot(conv_id, 
                "❌ Email илгээхэд алдаа гарлаа. SMTP тохиргоог шалгана уу эсвэл админтай холбогдоно уу."
            )
    
    elif needs_human and ENABLE_TEAMS_FALLBACK and not ENABLE_EMAIL_VERIFICATION:
        # Direct Teams notification without email verification (fallback)
        if contact_email:
            send_to_teams(
                user_email=contact_email,
                user_name=contact_name,
                question=text,
                conversation_id=conv_id
            )
            
            fallback_message = (
                "✅ Таны хүсэлтийг дэмжлэгийн багт илгээлээ. "
                "Тэд удахгүй танд хариулах болно."
            ) if human_requested else (
                "🔔 Таны асуулт нарийн туслалцаа шаардаж байна. "
                "Дэмжлэгийн багт илгээж байна."
            )
            
            send_to_chatwoot(conv_id, fallback_message)
        else:
            send_to_chatwoot(conv_id, 
                "⚠️ Дэмжлэгийн багт хүсэлт илгээхийн тулд профайлдаа email хаяг оруулна уу."
            )

    return jsonify({"status": "success"}), 200


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

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "crawl_status": crawl_status,
        "crawled_pages": len(crawled_data),
        "active_conversations": len(conversation_memory),
        "pending_verifications": len(email_verification_codes),
        "pending_support_requests": len(pending_support_requests),
        "config": {
            "root_url": ROOT_URL,
            "auto_crawl_enabled": AUTO_CRAWL_ON_START,
            "openai_configured": client is not None,
            "chatwoot_configured": bool(CHATWOOT_API_KEY and ACCOUNT_ID),
            "teams_configured": bool(TEAMS_WEBHOOK_URL),
            "teams_fallback_enabled": ENABLE_TEAMS_FALLBACK,
            "smtp_configured": bool(SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM_EMAIL),
            "email_verification_enabled": ENABLE_EMAIL_VERIFICATION
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
