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

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# —— Memory Storage —— #
conversation_memory = {}
crawled_data = []
crawl_status = {"status": "not_started", "message": "Crawling has not started yet"}

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
def send_to_teams(email: str, issue: str) -> bool:
    """Send issue to Microsoft Teams via webhook"""
    if not TEAMS_WEBHOOK_URL:
        logging.error("Teams webhook URL not configured")
        return False
        
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0076D7",
        "summary": f"Шинэ хүсэлт: {email}",
        "sections": [{
            "activityTitle": "Cloud.mn - Шинэ хүсэлт",
            "activitySubtitle": f"Хэрэглэгч: {email}",
            "activityImage": "https://docs.cloud.mn/logo.png",
            "facts": [{
                "name": "Хэрэглэгч:",
                "value": email
            }, {
                "name": "Асуудал:",
                "value": issue
            }, {
                "name": "Огноо:",
                "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }],
            "markdown": True
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
        logging.info(f"Issue sent to Teams for {email}")
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
        success = send_to_teams(verified_email, text)
        if success:
            response = "✅ Таны асуудлыг хүлээн авлаа. Бид тантай удахгүй холбогдох болно. Баярлалаа!"
            send_to_chatwoot(conv_id, response)
            return jsonify({"status": "success"}), 200
    
    # Try to answer with AI first
    ai_response = get_ai_response(text, conv_id, crawled_data)
    
    # Check if AI couldn't find good answer by searching crawled data
    search_results = search_in_crawled_data(text, max_results=1)
    
    # AI automatically determines if human help is needed based on:
    # 1. No search results found
    # 2. Question seems complex or specific
    # 3. User seems frustrated or unsatisfied
    needs_human_help = should_escalate_to_human(text, search_results, ai_response, history)
    
    if needs_human_help and not verified_email:
        escalation_response = """🤝 Таны асуултад AI-аар хариулахад хэцүү байна. Хүний тусламж авахыг санал болгож байна.

Хүний тусламж авахын тулд имэйл хаягаа оруулна уу. Бид таны имэйл хаягийг баталгаажуулсны дараа асуудлыг шийдвэрлэх болно."""
        
        send_to_chatwoot(conv_id, escalation_response)
    else:
        send_to_chatwoot(conv_id, ai_response)

    return jsonify({"status": "success"}), 200


def should_escalate_to_human(user_message: str, search_results: list, ai_response: str, history: list) -> bool:
    """AI determines if human help is needed based on context"""
    
    # Check if no relevant search results found
    if not search_results:
        return True
    
    # Check if search results have very low relevance
    if search_results and search_results[0].get('relevance_score', 0) < 2:
        return True
    
    # Use AI to determine if human help is needed
    if not client:
        return False  # If no OpenAI client, don't escalate
    
    # Build context for AI decision
    context = f"""Хэрэглэгчийн мессеж: "{user_message}"
Хайлтын үр дүн олдсон: {'Тийм' if search_results else 'Үгүй'}
AI хариулт: "{ai_response[:200]}..."

Ярилцлагын түүх:"""
    
    if history:
        recent_messages = [msg.get("content", "")[:100] for msg in history[-3:] if msg.get("role") == "user"]
        context += "\n" + "\n".join(recent_messages)
    
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": """Та хэрэглэгчийн хүсэлтийг шинжилж, хүний тусламж хэрэгтэй эсэхийг тодорхойлдог. 
                    
Дараах тохиолдлуудад хүний тусламж хэрэгтэй:
- Техникийн асуудал, алдаа, тохиргоо
- AI хариулт хангалтгүй, тохиромжгүй
- Хэрэглэгч сэтгэл дундуур, эргэлзэж байгаа
- Тусгай үйлчилгээ, өөрчлөлт хүсэж байгаа
- Гомдол, санал гэх мэт

Хариултаа зөвхөн 'YES' эсвэл 'NO' гэж өгнө үү."""
                },
                {
                    "role": "user", 
                    "content": context
                }
            ],
            max_tokens=10,
            temperature=0.3
        )
        
        ai_decision = response.choices[0].message.content.strip().upper()
        return ai_decision == "YES"
        
    except Exception as e:
        logging.error(f"AI escalation decision error: {e}")
        # Fallback logic if AI fails
        return len(user_message) > 30 and not search_results


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
        "config": {
            "root_url": ROOT_URL,
            "auto_crawl_enabled": AUTO_CRAWL_ON_START,
            "openai_configured": client is not None,
            "chatwoot_configured": bool(CHATWOOT_API_KEY and ACCOUNT_ID)
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

Хэрэв та энэ хүсэлтийг илгээгээгүй бол энэ имэйлийг үл тоомсорлоно уу.

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
