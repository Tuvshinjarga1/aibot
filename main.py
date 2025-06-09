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


# —— AI Analysis Functions —— #
def analyze_user_message_with_ai(user_message: str, ai_response: str, conv_id: int):
    """Use AI to analyze if user needs support team or matches services"""
    if not client:
        return {"needs_support": False, "matching_services": [], "confidence": 0}
    
    try:
        # Create service list for AI analysis
        service_list = "\n".join([f"- {key}" for key in SERVICE_PRICES.keys()])
        
        analysis_prompt = f"""
Хэрэглэгчийн асуулт болон AI хариултыг дүгнэж, дараах асуултуудад хариулна уу:

1. Хэрэглэгч дэмжлэгийн багтай холбогдох шаардлагатай юу? (техникийн асуудал, төвөгтэй асуудал, AI хариулт хангалтгүй)
2. Хэрэглэгчийн асуулт дараах үйлчилгээнүүдтэй тохирч байна уу?

Үйлчилгээний жагсаалт:
{service_list}

Хэрэглэгчийн асуулт: {user_message}
AI хариулт: {ai_response}

Хариултаа JSON форматаар өг:
{{
    "needs_support": true/false,
    "confidence": 0-100,
    "reason": "яагаад дэмжлэг хэрэгтэй болох шалтгаан",
    "matching_services": ["тохирох үйлчилгээний нэр1", "тохирох үйлчилгээний нэр2"],
    "suggested_action": "санал болгох үйлдэл"
}}
        """
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system", 
                    "content": "Та мэргэжлийн дүгнэлт хийгч. Хэрэглэгчийн хэрэгцээг тодорхойлж, зөв шийдэл санал болгож чаддаг."
                },
                {
                    "role": "user", 
                    "content": analysis_prompt
                }
            ],
            max_tokens=300,
            temperature=0.3
        )
        
        analysis_text = response.choices[0].message.content.strip()
        
        # Try to parse JSON response
        import re
        json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
        if json_match:
            analysis_json = json.loads(json_match.group())
            return analysis_json
        else:
            # Fallback analysis
            return {
                "needs_support": any(keyword in user_message.lower() for keyword in ["алдаа", "ажилахгүй", "асуудал", "тусламж"]),
                "matching_services": [],
                "confidence": 50,
                "reason": "JSON parse хийх боломжгүй",
                "suggested_action": "Manual review шаардлагатай"
            }
            
    except Exception as e:
        logging.error(f"AI analysis алдаа: {e}")
        return {"needs_support": False, "matching_services": [], "confidence": 0}

def suggest_services_from_analysis(matching_services: list):
    """Generate service suggestions based on analysis"""
    if not matching_services:
        return ""
    
    suggestions = "💡 **Таны асуудалтай холбоотой үйлчилгээнүүд:**\n\n"
    
    for service_name in matching_services:
        if service_name in SERVICE_PRICES:
            service_info = SERVICE_PRICES[service_name]
            suggestions += f"🔧 **{service_name}**\n"
            suggestions += f"   💰 Үнэ: {service_info['price']}\n"
            suggestions += f"   📝 Тайлбар: {service_info['desc']}\n\n"
    
    suggestions += "📞 Эдгээр үйлчилгээний талаар дэлгэрэнгүй мэдээлэл авахыг хүсвэл 'дэмжлэг' гэж бичнэ үү."
    return suggestions

# —— Enhanced AI Response with Smart Analysis —— #


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
def send_to_teams(message: str, title: str = "Cloud.mn AI Assistant", color: str = "0076D7", conv_id: int = None):
    """Send message to Microsoft Teams channel using webhook"""
    if not TEAMS_WEBHOOK_URL:
        logging.warning("Teams webhook URL not configured")
        return False
        
    try:
        sections = [
            {
                "activityTitle": title,
                "activitySubtitle": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "text": message,
                "markdown": True
            }
        ]
        
        # Add Chatwoot URL section if conv_id is provided
        if conv_id:
            sections.append({
                "text": f"<a href='{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}'>Chatwoot дээр харах</a>",
                "markdown": False
            })
        
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color,
            "summary": title,
            "sections": sections
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

def send_teams_notification(conv_id: int, message: str, message_type: str = "outgoing", is_unsolved: bool = False, confirmed: bool = False, user_email: str = None):
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
        
        # Only send to Teams if confirmed
        if confirmed:
            # Get email from conversation or use contact email as fallback
            display_email = user_email if user_email else contact_email
            
            # Create Teams message with simpler format
            teams_message = f"""
Cloud.mn AI - {contact_name}
{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

💬 Шинэ мессэж

Хэрэглэгч:
Нэр: {contact_name}
Имэйл: {display_email}
Харилцан ярианы ID: {conv_id}

Алдаа: {message}
            """
            
            # Send to Teams with HTML format
            send_to_teams(
                message=teams_message,
                title=f"Cloud.mn AI - {contact_name}",
                color="0076D7",  # Default blue color
                conv_id=conv_id  # Pass conv_id for Chatwoot URL
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
                # Email хаяг асуух
                email_request = """
✅ Баярлалаа! Таны асуудлыг дэмжлэгийн багтай хуваалцахын тулд email хаягаа өгнө үү?

📧 **Email хаяг оруулна уу:**
Жишээ: example@gmail.com

Энэ нь дэмжлэгийн багт танай холбогдох мэдээллийг илгээхэд ашиглагдана.
                """
                send_to_chatwoot(conv_id, email_request)
                
                # Mark as waiting for email
                conversation_memory[conv_id].append({"role": "assistant", "content": "waiting_for_email"})
            else:
                send_to_chatwoot(conv_id, "✅ Ойлголоо. Таны асуудлыг дэмжлэгийн баг руу илгээхгүй байх болно.")
                
        # Check if waiting for email address
        elif memory and "waiting_for_email" in memory[-1].get("content", ""):
            # Allow user to cancel email request
            if text.lower() in ["цуцлах", "cancel", "үгүй", "no", "болих"]:
                send_to_chatwoot(conv_id, "✅ Email хаяг өгөх хүсэлтийг цуцаллаа. Та дараа дахин хүсэлт илгээж болно.")
                # Clear waiting state
                conversation_memory[conv_id] = [msg for msg in conversation_memory[conv_id] if "waiting_for_email" not in msg.get("content", "")]
                return jsonify({"status": "success"}), 200
            
            # Validate email format
            import re
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            
            if re.match(email_pattern, text.strip()):
                user_email = text.strip()
                
                # Get the original question and AI response
                original_question = None
                ai_response = None
                
                for i, msg in enumerate(memory):
                    if "pending_confirmation" in msg.get("content", ""):
                        if i >= 2:
                            ai_response = memory[i-1].get("content", "")
                            original_question = memory[i-2].get("content", "")
                        break
                
                # Send to Teams with email
                send_teams_notification(
                    conv_id,
                    f"AI хариулт: {ai_response}\n\nХэрэглэгчийн асуулт: {original_question}\n\nИмэйл хаяг: {user_email}",
                    "outgoing",
                    is_unsolved=True,
                    confirmed=True,
                    user_email=user_email
                )
                
                send_to_chatwoot(conv_id, f"✅ Баярлалаа! Таны асуудлыг ({user_email}) дэмжлэгийн баг руу илгээлээ. Тун удахгүй холбогдох болно.")
                
                # Clear waiting state
                conversation_memory[conv_id] = [msg for msg in conversation_memory[conv_id] if "waiting_for_email" not in msg.get("content", "")]
                
            else:
                send_to_chatwoot(conv_id, "❌ Буруу email хэлбэр байна. Зөв email хаяг оруулна уу (жишээ: example@gmail.com)\n\n💡 'цуцлах' гэж бичвэл email өгөхгүйгээр гарж болно.")
                
        else:
            # General AI conversation
            # send_to_chatwoot(conv_id, "🤔 Боловсруулж байна...")
            ai_response = get_ai_response(text, conv_id, crawled_data)
            send_to_chatwoot(conv_id, ai_response)
            
            # Smart AI Analysis - дүгнэлт хийх
            analysis = analyze_user_message_with_ai(text, ai_response, conv_id)
            
            # Холбогдох үйлчилгээ санал болгох
            if analysis.get("matching_services"):
                service_suggestions = suggest_services_from_analysis(analysis["matching_services"])
                if service_suggestions:
                    send_to_chatwoot(conv_id, service_suggestions)
            
            # Дэмжлэгийн багтай холбогдох шаардлагатай эсэхийг шалгах
            needs_support = analysis.get("needs_support", False)
            confidence = analysis.get("confidence", 0)
            
            if needs_support and confidence > 60:
                # Өндөр итгэлтэйгээр дэмжлэг хэрэгтэй гэж үзэж байвал
                confirmation_message = f"""
❓ Таны асуудлыг шийдвэрлэхэд мэргэжлийн дэмжлэг шаардлагатай байх магадлалтай. Дэмжлэгийн баг руу илгээх үү?

🔍 **Дүгнэлт:** {analysis.get('reason', 'Техникийн дэмжлэг шаардлагатай')}
📊 **Итгэлийн түвшин:** {confidence}%

Зөвшөөрч байвал "тийм" эсвэл "зөвшөөрч байна" гэж бичнэ үү.
Зөвшөөрөхгүй бол "үгүй" эсвэл "зөвшөөрөхгүй" гэж бичнэ үү.
                """
                send_to_chatwoot(conv_id, confirmation_message)
                
                # Store the conversation with pending confirmation
                if conv_id not in conversation_memory:
                    conversation_memory[conv_id] = []
                conversation_memory[conv_id].append({"role": "assistant", "content": confirmation_message + " pending_confirmation"})
                
            elif needs_support and confidence > 30:
                # Дунд зэргийн итгэлтэйгээр илүү мэдээлэл асуух
                clarification_message = f"""
🤔 Танай асуудлыг илүү сайн ойлгохын тулд нэмэлт мэдээлэл хэрэгтэй байна.

📋 **Дэлгэрэнгүй мэдээлэл өгнө үү:**
• Ямар алдаа гарч байна?
• Хэзээнээс эхэлсэн асуудал вэ?
• Ямар системд/серверт асуудал гарч байна?

Эсвэл "дэмжлэг" гэж бичвэл мэргэжлийн баг руу холбож өгөх болно.
                """
                send_to_chatwoot(conv_id, clarification_message)
            
            # Шаардлагагүй бол Teams рүү илгээхгүй
            # Зөвхөн үндсэн AI хариулт л хангалттай

    # Үйлчилгээний үнэ харуулах
    services = get_services_in_text(text)
    if services:
        price_msg = "💡 Та дараах үйлчилгээ(үүд)-ийн үнийн мэдээлэл:\n"
        for key, info in services:
            price_msg += f"\n• {info['desc']}\n   ➡️ Үнэ: {info['price']}\n"
        send_to_chatwoot(conv_id, price_msg)

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


# 1. Үйлчилгээний нэрсийн жагсаалт - SERVICE_PRICES-тэй тохирох
SERVICE_KEYWORDS = [
    "Nginx", "apache2", "httpd", "php", "wordpress", "phpMyAdmin", "сервер суулгах", "сервис суулгах",
    "Database", "SQL", "NoSQL", "өгөгдлийн сан",
    "VPN тохируулах", "VPN", "виртуал нэтворк",
    "Хэрэглэгч хооронд сервер зөөх", "сервер зөөх", "файл зөөх", "migration",
    "Windows сервер", "Windows лиценз", "лиценз тохируулах",
    "серверийг үүсгэж өгөх", "порт тохируулах", "firewall", "network",
    "DNS record", "DNS тохируулах", "домэйн",
    "мэйл сервер", "email server", "smtp", "pop3", "imap",
    "нууц үг сэргээх", "password reset", "хандалт сэргээх",
    "SSL тохируулах", "SSL сертификат", "HTTPS", "шифрлэлт",
    "нүүдэл сэргээх", "backup restore", "сэргээх",
    "файл хуулах", "local руу хуулах", "download", "file transfer",
    "сүлжээний алдаа", "network error", "connectivity issue",
    "аюулгүй байдал", "security", "аудит", "log цуглуулах",
    "физик сервер", "VPS", "виртуал машин", "клауд сервер",
    "технологийн зөвлөх", "consulting", "зөвлөгөө",
    "серверийн алдаа", "system error", "debugging", "troubleshooting"
]

# 2. Дэмжлэг хүссэн түлхүүр үгс
SUPPORT_KEYWORDS = [
    "дэмжлэг", "support", "тусламж", "зөвлөгөө", "холбогдох", "operator", "help", "админ",
    "алдаа", "асуудал", "problem", "issue", "bug", "ажилахгүй", "broken"
]

def contains_service_or_support(text):
    """Enhanced service and support detection"""
    text_lower = text.lower()
    found_service = any(service.lower() in text_lower for service in SERVICE_KEYWORDS)
    found_support = any(word in text_lower for word in SUPPORT_KEYWORDS)
    return found_service or found_support

def get_services_in_text(text):
    """Enhanced service detection in text"""
    found = []
    text_lower = text.lower()
    
    for key, info in SERVICE_PRICES.items():
        # Check if service name or related keywords are mentioned
        service_keywords = key.lower().split()
        if any(keyword in text_lower for keyword in service_keywords):
            found.append((key, info))
        
        # Also check SERVICE_KEYWORDS for broader matching
        for keyword in SERVICE_KEYWORDS:
            if keyword.lower() in text_lower and keyword.lower() in key.lower():
                if (key, info) not in found:
                    found.append((key, info))
    
    return found


# —— New Service Prices —— #
SERVICE_PRICES = {
    "Nginx, apache2, httpd, php, wordpress, phpMyAdmin зэрэг сервис суулгах": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Nginx, apache2, httpd, php, wordpress, phpMyAdmin зэрэг сервис суулгах",
        "server_inside": True, "server_outside": False, "duration": "10min per service only for installation"
    },
    "Database, SQL, NoSQL сервис суулгах": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Database, SQL, NoSQL сервис суулгах",
        "server_inside": True, "server_outside": False, "duration": "10min per service only for installation"
    },
    "VPN тохируулах": {
        "price": "Ажлын цагаар 88,000₮, Ажлын бус цагаар 110,000₮",
        "desc": "VPN тохируулах",
        "server_inside": True, "server_outside": False, "duration": "60-120"
    },
    "Хэрэглэгч хооронд сервер зөөх": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Хэрэглэгч хооронд сүлжээ зөөх",
        "server_inside": True, "server_outside": False, "duration": "Дискийн хэмжээнээс хамаарна. 20min for 15GB"
    },
    "Windows сервер дээр лиценз тохируулах": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Windows сервер дээр шинээр тохируулах",
        "server_inside": True, "server_outside": False, "duration": "30-60"
    },
    "Хэрэглэгчийн серверийг үүсгэж өгөх, порт тохируулах": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Хэрэглэгчийн серверийн үүрэг, порт тохируулах",
        "server_inside": True, "server_outside": False, "duration": "20"
    },
    "DNS record тохируулах": {
        "price": "Ажлын цагаар 33,000₮, Ажлын бус цагаар 55,000₮",
        "desc": "DNS record тохируулах",
        "server_inside": True, "server_outside": False, "duration": "30-60"
    },
    "Мэйл сервер дээр туслалцаа үзүүлэх": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Серверээс өгөгдөл сэргээх",
        "server_inside": True, "server_outside": False, "duration": "30 +"
    },
    "Хэрэглэгчийн нууц үг сэргээх": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Хэрэглэгчийн нүүгдэл сэргээх",
        "server_inside": True, "server_outside": False, "duration": "30 +"
    },
    "SSL тохируулах": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "SSL тохируулах",
        "server_inside": True, "server_outside": False, "duration": "30-60"
    },
    "Хэрэглэгчийн нүүдэл сэргээх": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Хэрэглэгчийн нүүдэл сэргээх",
        "server_inside": True, "server_outside": False, "duration": "30 +"
    },
    "Клауд серверээс local руу файл хуулах": {
        "price": "Ажлын цагаар 77,000₮, Ажлын бус цагаар 110,000₮",
        "desc": "Клауд серверээс local руу файл хуулах",
        "server_inside": True, "server_outside": False, "duration": "Файлын хэмжээ, интернетийн хурднаас хамаарна. Урьдчилан хугацаа тодорхойлох боломжгүй."
    },
    "Сүлжээний буруу тохиргооноос үүссэн алдаа засварлах": {
        "price": "Ажлын цагаар 55,000₮, Ажлын бус цагаар 88,000₮",
        "desc": "Сүлжээний буруу тохиргооноос үүссэн алдаа засварлах",
        "server_inside": True, "server_outside": False, "duration": "Алдааны хэмжээнээс хамаарна. Урьдчилан хугацаа тодорхойлох боломжгүй."
    },
    "Физик сервер, VPS болон бусад клауд виртуал машин/сервер үүсгэх": {
        "price": "Ажлын цагаар 110,000₮, Ажлын бус цагаар 132,000₮",
        "desc": "Физик сервер, VPS болон бусад клауд виртуал машин/сервер үүсгэх",
        "server_inside": False, "server_outside": True, "duration": "хамаарна"
    },
    "Технологийн бусад төрлийн зөвлөх үйлчилгээ": {
        "price": "Ажлын цагаар 110,000₮, Ажлын бус цагаар 132,000₮",
        "desc": "Технологийн бусад төрлийн зөвлөх үйлчилгээ",
        "server_inside": True, "server_outside": True, "duration": "60"
    },
    "Нийлүүлэгчээс шаардлагатай серверийн дотоод алдаа илрүүлэх, засварлах": {
        "price": "110,000₮ (ажлын цагаар), 132,000₮ (ажлын бус цагаар)",
        "desc": "Нийлүүлэгчээс шаардлагатай серверийн дотоод алдаа илрүүлэх, засварлах",
        "server_inside": True, "server_outside": False, "duration": "Алдааны хэмжээнээс хамаарна, урьдчилан хугацаа тодорхойлох боломжгүй."
    }
}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
