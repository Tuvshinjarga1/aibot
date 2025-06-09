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
TEAMS_WEBHOOK_URL    = os.getenv("TEAMS_WEBHOOK_URL")  # Microsoft Teams webhook URL

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
    """Enhanced AI response with better context awareness and image support"""
    
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
                # Find the page in crawled_data to get images
                page = next((p for p in crawled_data if p['url'] == result['url']), None)
                if page and page.get('images'):
                    image_info = "\nЗургууд:\n" + "\n".join([
                        f"- {img['alt']}: {img['url']}" if img['alt'] else f"- {img['url']}"
                        for img in page['images']
                    ])
                else:
                    image_info = ""
                
                relevant_pages.append(
                    f"Хуудас: {result['title']}\n"
                    f"URL: {result['url']}\n"
                    f"Холбогдох агуулга: {result['snippet']}\n"
                    f"{image_info}\n"
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
    5. Хэрэв холбогдох зургууд байвал тэдгээрийг хариултад оруулаарай
    
    Зургийн мэдээллийг хариултад оруулахдаа:
    - Зургийн тайлбар (alt text) байвал түүнийг ашиглаарай
    - Зургийн URL-ийг хариултад оруулаарай
    - Зургийн талаар товч тайлбар өгөөрэй
    
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
            max_tokens=800,  # Increased token limit for better responses with images
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
        return f"🔧 AI-тай холбогдоход саад гарлаа. Та дараах аргуудаар тусламж авч болно:\n• 'help' командыг ашиглана уу\n• 'crawl' эсвэл 'search' командуудыг туршина уу\n\nАлдааны дэлгэрэнгүй: {str(e)[:100]}"

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


# —— Teams Integration —— #
def send_to_teams(message: str, title: str = "Cloud.mn AI Assistant", color: str = "0076D7"):
    """Send message to Microsoft Teams channel using webhook"""
    if not TEAMS_WEBHOOK_URL:
        logging.warning("Teams webhook URL not configured")
        return False
        
    try:
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color,
            "summary": title,
            "sections": [{
                "activityTitle": title,
                "activitySubtitle": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "text": message,
                "markdown": True
            }]
        }
        
        response = requests.post(
            TEAMS_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        logging.info("Message sent to Teams successfully")
        return True
        
    except Exception as e:
        logging.error(f"Failed to send message to Teams: {e}")
        return False

def send_teams_notification(conv_id: int, message: str, message_type: str = "outgoing", is_unsolved: bool = False, confirmed: bool = False):
    """Send notification to Teams about new conversation or message"""
    if not TEAMS_WEBHOOK_URL:
        return
        
    try:
        # Get conversation info
        conv_info = get_conversation_info(conv_id)
        if not conv_info:
            return
            
        contact = conv_info.get("contact", {})
        contact_name = contact.get("name", "Хэрэглэгч")
        contact_email = contact.get("email", "Имэйл олдсонгүй")
        
        # Create Teams message
        if is_unsolved and confirmed:
            teams_message = f"""
### ⚠️ Шийдэгдээгүй асуудал (Зөвшөөрөлтэй)

**Хэрэглэгч:**
- Нэр: {contact_name}
- Имэйл: {contact_email}
- Харилцан ярианы ID: {conv_id}

**Асуудлын дэлгэрэнгүй:**
{message}

**Харилцан ярианы түүх:**
{get_conversation_history(conv_id)}

[Chatwoot дээр харах]({CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id})
            """
            color = "FF0000"  # Red for unsolved issues
        elif is_unsolved and not confirmed:
            teams_message = f"""
### ⚠️ Шийдэгдээгүй асуудал (Зөвшөөрөл хүлээж байна)

**Хэрэглэгч:**
- Нэр: {contact_name}
- Имэйл: {contact_email}
- Харилцан ярианы ID: {conv_id}

**Асуудлын дэлгэрэнгүй:**
{message}

**Харилцан ярианы түүх:**
{get_conversation_history(conv_id)}

[Chatwoot дээр харах]({CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id})
            """
            color = "FFA500"  # Orange for pending confirmation
        else:
            teams_message = f"""
### 💬 Шинэ мессэж

**Хэрэглэгч:**
- Нэр: {contact_name}
- Имэйл: {contact_email}
- Харилцан ярианы ID: {conv_id}

**Мессэж:**
{message}

[Chatwoot дээр харах]({CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id})
            """
            color = "0076D7" if message_type == "incoming" else "00FF00"
        
        # Send to Teams
        send_to_teams(
            message=teams_message,
            title=f"Cloud.mn AI - {contact_name}",
            color=color
        )
        
    except Exception as e:
        logging.error(f"Failed to send Teams notification: {e}")

def get_conversation_history(conv_id: int, max_messages: int = 5):
    """Get recent conversation history"""
    try:
        memory = conversation_memory.get(conv_id, [])
        if not memory:
            return "Харилцан ярианы түүх олдсонгүй"
            
        history = []
        for msg in memory[-max_messages:]:
            role = "👤 Хэрэглэгч" if msg["role"] == "user" else "🤖 AI"
            history.append(f"{role}: {msg['content']}")
            
        return "\n\n".join(history)
    except Exception as e:
        logging.error(f"Failed to get conversation history: {e}")
        return "Харилцан ярианы түүхийг ачаалахад алдаа гарлаа"


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
    """Enhanced webhook with better AI integration and Teams notifications"""
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
    
    # Send notification to Teams
    send_teams_notification(conv_id, text, "incoming")
    
    # Handle different commands
    if text.lower() == "crawl":
        # Check if auto-crawl already completed
        if crawl_status["status"] == "completed":
            response = f"✅ Сайт аль хэдийн шүүрдэгдсэн байна! {crawl_status.get('pages_count', 0)} хуудас бэлэн.\n\n'search <асуулт>' командаар хайлт хийж болно!"
            send_to_chatwoot(conv_id, response)
            send_teams_notification(conv_id, f"Сайт шүүрдэгдсэн байна. {crawl_status.get('pages_count', 0)} хуудас бэлэн.", "outgoing")
        elif crawl_status["status"] == "running":
            send_to_chatwoot(conv_id, "🔄 Сайт одоо шүүрдэгдэж байна. Түр хүлээнэ үү...")
        else:
            send_to_chatwoot(conv_id, f"🔄 Сайн байна уу {contact_name}! Сайтыг шүүрдэж байна...")
            
            crawl_status = {"status": "running", "message": f"Manual crawl started by {contact_name}"}
            crawled_data = crawl_and_scrape(ROOT_URL)
            
            if not crawled_data:
                crawl_status = {"status": "failed", "message": "Manual crawl failed"}
                send_to_chatwoot(conv_id, "❌ Шүүрдэх явцад алдаа гарлаа. Дахин оролдоно уу.")
                send_teams_notification(conv_id, "❌ Сайт шүүрдэхэд алдаа гарлаа", "outgoing")
            else:
                crawl_status = {
                    "status": "completed", 
                    "message": f"Manual crawl completed by {contact_name}",
                    "pages_count": len(crawled_data),
                    "timestamp": datetime.now().isoformat()
                }
                lines = [f"📄 {p['title']} — {p['url']}" for p in crawled_data[:3]]
                response = f"✅ {len(crawled_data)} хуудас амжилттай шүүрдлээ!\n\nЭхний 3 хуудас:\n" + "\n".join(lines) + f"\n\nОдоо 'search <асуулт>' командаар хайлт хийж болно!"
                send_to_chatwoot(conv_id, response)
                send_teams_notification(conv_id, f"✅ {len(crawled_data)} хуудас амжилттай шүүрдлээ!", "outgoing")

    elif text.lower().startswith("scrape"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_to_chatwoot(conv_id, "⚠️ Зөв хэлбэр: `scrape <бүрэн-URL>`")
        else:
            url = parts[1].strip()
            send_to_chatwoot(conv_id, f"🔄 {url} хаягыг шүүрдэж байна...")
            
            try:
                page = scrape_single(url)
                summary = get_ai_response(f"Энэ агуулгыг товчлон хэлээрэй: {page['body'][:1500]}", conv_id)
                
                response = f"📄 **{page['title']}**\n\n📝 **Товчилсон агуулга:**\n{summary}\n\n🔗 {url}"
                send_to_chatwoot(conv_id, response)
                send_teams_notification(conv_id, f"📄 {page['title']} хуудсыг шүүрдлээ", "outgoing")
            except Exception as e:
                error_msg = f"❌ {url} хаягыг шүүрдэхэд алдаа гарлаа: {e}"
                send_to_chatwoot(conv_id, error_msg)
                send_teams_notification(conv_id, error_msg, "outgoing")

    elif text.lower().startswith("search"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_to_chatwoot(conv_id, "⚠️ Зөв хэлбэр: `search <хайх үг>`")
        else:
            query = parts[1].strip()
            
            # Check crawl status first
            if crawl_status["status"] == "running":
                send_to_chatwoot(conv_id, "🔄 Сайт шүүрдэгдэж байна. Түр хүлээгээд дахин оролдоно уу.")
            elif crawl_status["status"] in ["not_started", "failed", "error"] or not crawled_data:
                send_to_chatwoot(conv_id, "📚 Мэдээлэл бэлэн байхгүй байна. 'crawl' командыг ашиглан сайтыг шүүрдүүлнэ үү.")
            else:
                send_to_chatwoot(conv_id, f"🔍 '{query}' хайж байна...")
                
                results = search_in_crawled_data(query)
                if results:
                    response = f"🔍 '{query}' хайлтын үр дүн ({len(results)} илэрц):\n\n"
                    for i, result in enumerate(results, 1):
                        response += f"{i}. **{result['title']}**\n"
                        response += f"   {result['snippet']}\n"
                        response += f"   🔗 {result['url']}\n\n"
                    
                    send_to_chatwoot(conv_id, response)
                    send_teams_notification(conv_id, f"🔍 '{query}' хайлтын үр дүн: {len(results)} илэрц олдлоо", "outgoing")
                else:
                    response = f"❌ '{query}' хайлтаар илэрц олдсонгүй."
                    send_to_chatwoot(conv_id, response)
                    send_teams_notification(conv_id, response, "outgoing")

    elif text.lower() in ["help", "тусламж"]:
        # Show status-aware help
        status_info = ""
        if crawl_status["status"] == "completed":
            status_info = f"✅ {crawl_status.get('pages_count', 0)} хуудас бэлэн байна.\n"
        elif crawl_status["status"] == "running":
            status_info = "🔄 Сайт шүүрдэгдэж байна.\n"
        elif crawl_status["status"] == "disabled":
            status_info = "⚠️ Автомат шүүрдэх идэвхгүй байна.\n"
        
        help_text = f"""
👋 Сайн байна уу {contact_name}! Би Cloud.mn-ийн AI туслах юм.

📊 **Төлөв:**
{status_info}

🤖 **Боломжит командууд:**
• `crawl` - Сайтыг шүүрдэх (шаардлагатай бол)
• `scrape <URL>` - Тодорхой хуудас шүүрдэх
• `search <асуулт>` - Мэдээлэл хайх
• `help` - Энэ тусламжийг харуулах

💬 **Чөлөөт ярилцлага:**
Та мөн надад асуулт асууж, ярилцаж болно. Би монгол хэлээр хариулна.

⏰ Үргэлж тусламжид бэлэн байна!
        """
        send_to_chatwoot(conv_id, help_text)
        send_teams_notification(conv_id, f"ℹ️ {contact_name} тусламж хүссэн", "outgoing")

    elif text.lower() in ["баяртай", "goodbye", "баай"]:
        response = f"👋 Баяртай {contact_name}! Дараа уулзацгаая!"
        send_to_chatwoot(conv_id, response)
        send_teams_notification(conv_id, response, "outgoing")
        mark_conversation_resolved(conv_id)

    else:
        # Check if this is a response to a confirmation request
        memory = conversation_memory.get(conv_id, [])
        if memory and "pending_confirmation" in memory[-1].get("content", ""):
            # Use GPT to understand the response
            confirmation_response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {
                        "role": "system",
                        "content": """Та хэрэглэгчийн хариултыг дүгнэж, зөвшөөрөл эсвэл татгалзлыг тодорхойлох ёстой.
                        Хариултад 'yes' эсвэл 'no' гэж бичнэ үү."""
                    },
                    {
                        "role": "user",
                        "content": f"Хэрэглэгчийн хариулт: {text}\n\nЭнэ нь зөвшөөрөл мөн үү, эсвэл татгалзвал мөн үү?"
                    }
                ],
                max_tokens=10,
                temperature=0.3
            )
            
            is_confirmed = confirmation_response.choices[0].message.content.strip().lower() == "yes"
            
            if is_confirmed:
                # Send to Teams with confirmation
                send_teams_notification(
                    conv_id,
                    f"AI хариулт: {memory[-2]['content']}\n\nХэрэглэгчийн асуулт: {memory[-3]['content']}",
                    "outgoing",
                    is_unsolved=True,
                    confirmed=True
                )
                send_to_chatwoot(conv_id, "✅ Баярлалаа! Таны асуудлыг дэмжлэгийн баг руу илгээлээ. Тун удахгүй холбогдох болно.")
            else:
                send_to_chatwoot(conv_id, "✅ Ойлголоо. Таны асуудлыг дэмжлэгийн баг руу илгээхгүй байх болно.")
        else:
            # General AI conversation
            send_to_chatwoot(conv_id)
            # send_to_chatwoot(conv_id, "🤔 Боловсруулж байна...")
            ai_response = get_ai_response(text, conv_id, crawled_data)
            send_to_chatwoot(conv_id, ai_response)
            
            # Check if AI couldn't help
            if any(keyword in ai_response.lower() for keyword in ["ойлгомжгүй", "тодорхойгүй", "алдаа", "саад"]):
                # Ask for confirmation before sending to Teams
                confirmation_message = f"""
❓ Таны асуудлыг шийдвэрлэхэд хүндрэлтэй байна. Дэмжлэгийн баг руу илгээх үү?

Асуулт: {text}
AI хариулт: {ai_response}

Зөвшөөрч байвал "тийм" эсвэл "зөвшөөрч байна" гэж бичнэ үү.
Зөвшөөрөхгүй бол "үгүй" эсвэл "зөвшөөрөхгүй" гэж бичнэ үү.
                """
                send_to_chatwoot(conv_id, confirmation_message)
                
                # Store the conversation with pending confirmation
                if conv_id not in conversation_memory:
                    conversation_memory[conv_id] = []
                conversation_memory[conv_id].append({"role": "assistant", "content": confirmation_message + " pending_confirmation"})
                
                # Send to Teams as pending confirmation
                send_teams_notification(
                    conv_id,
                    f"AI хариулт: {ai_response}\n\nХэрэглэгчийн асуулт: {text}",
                    "outgoing",
                    is_unsolved=True,
                    confirmed=False
                )
            else:
                send_teams_notification(
                    conv_id,
                    f"💬 {contact_name}-ийн асуулт: {text}\n\n🤖 AI хариулт: {ai_response}",
                    "outgoing"
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
        "config": {
            "root_url": ROOT_URL,
            "auto_crawl_enabled": AUTO_CRAWL_ON_START,
            "openai_configured": client is not None,
            "chatwoot_configured": bool(CHATWOOT_API_KEY and ACCOUNT_ID)
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
