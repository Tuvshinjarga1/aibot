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
ROOT_URL             = "https://docs.cloud.mn/"
DELAY_SEC            = 0.5
ALLOWED_NETLOC       = urlparse(ROOT_URL).netloc
MAX_CRAWL_PAGES      = 50
CHATWOOT_API_KEY     = os.getenv("CHATWOOT_API_KEY")
ACCOUNT_ID           = os.getenv("ACCOUNT_ID")
CHATWOOT_BASE_URL    = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com")
OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# —— Memory Storage —— #
conversation_memory = {}
crawled_data = []

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

def scrape_single(url: str):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title else url
    body, images = extract_content(soup, url)
    return {"url": url, "title": title, "body": body, "images": images}


# —— AI Assistant Functions —— #
def get_ai_response(user_message: str, conversation_id: int, context_data: list = None):
    """Enhanced AI response with context awareness"""
    
    if not client:
        return "🔑 OpenAI API түлхүүр тохируулагдаагүй байна. Админтай холбогдоно уу."
    
    # Get conversation history
    history = conversation_memory.get(conversation_id, [])
    
    # Build context from crawled data if available
    context = ""
    if context_data and crawled_data:
        relevant_pages = []
        for page in crawled_data[:3]:  # Use first 3 pages as context
            relevant_pages.append(f"Хуудас: {page['title']}\nАгуулга: {page['body'][:300]}...")
        context = "\n\n".join(relevant_pages)
    
    # Build conversation context
    messages = [
        {
            "role": "system", 
            "content": f"""Та Cloud.mn-ийн баримт бичгийн талаар асуултад хариулдаг Монгол AI туслах юм. 
            Хэрэглэгчтэй монгол хэлээр ярилцаарай. Хариултаа товч бөгөөд ойлгомжтой байлгаарай.
            
            Боломжит командууд:
            - crawl: Бүх сайтыг шүүрдэх
            - scrape <URL>: Тодорхой хуудсыг шүүрдэх  
            - help: Тусламж харуулах
            - search <асуулт>: Мэдээлэл хайх
            
            {f"Контекст мэдээлэл:\\n{context}" if context else ""}
            """
        }
    ]
    
    # Add conversation history
    for msg in history[-4:]:  # Last 4 messages
        messages.append(msg)
    
    # Add current message
    messages.append({"role": "user", "content": user_message})
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=300,
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
    """Search through crawled data"""
    if not crawled_data:
        return []
    
    query_lower = query.lower()
    results = []
    
    for page in crawled_data:
        title_match = query_lower in page['title'].lower()
        body_match = query_lower in page['body'].lower()
        
        if title_match or body_match:
            results.append({
                'title': page['title'],
                'url': page['url'],
                'snippet': page['body'][:200] + "..." if len(page['body']) > 200 else page['body']
            })
            
        if len(results) >= max_results:
            break
            
    return results


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
    """Enhanced webhook with better AI integration"""
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
        send_to_chatwoot(conv_id, f"🔄 Сайн байна уу {contact_name}! Сайтыг шүүрдэж байна, түр хүлээнэ үү...")
        
        global crawled_data
        crawled_data = crawl_and_scrape(ROOT_URL)
        
        if not crawled_data:
            send_to_chatwoot(conv_id, "❌ Шүүрдэх явцад алдаа гарлаа. Дахин оролдоно уу.")
        else:
            lines = [f"📄 {p['title']} — {p['url']}" for p in crawled_data[:3]]
            send_to_chatwoot(conv_id,
                f"✅ {len(crawled_data)} хуудас амжилттай шүүрдлээ!\n\n"
                f"Эхний 3 хуудас:\n" + "\n".join(lines) + 
                f"\n\nОдоо 'search <асуулт>' командаар хайлт хийж болно!"
            )

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
                
                send_to_chatwoot(conv_id,
                    f"📄 **{page['title']}**\n\n"
                    f"📝 **Товчилсон агуулга:**\n{summary}\n\n"
                    f"🔗 {url}"
                )
            except Exception as e:
                send_to_chatwoot(conv_id, f"❌ {url} хаягыг шүүрдэхэд алдаа гарлаа: {e}")

    elif text.lower().startswith("search"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_to_chatwoot(conv_id, "⚠️ Зөв хэлбэр: `search <хайх үг>`")
        else:
            query = parts[1].strip()
            
            if not crawled_data:
                send_to_chatwoot(conv_id, 
                    "📚 Эхлээд 'crawl' командыг ашиглан сайтыг шүүрдүүлнэ үү."
                )
            else:
                send_to_chatwoot(conv_id, f"🔍 '{query}' хайж байна...")
                
                results = search_in_crawled_data(query)
                if results:
                    response = f"🔍 '{query}' хайлтын үр дүн:\n\n"
                    for i, result in enumerate(results, 1):
                        response += f"{i}. **{result['title']}**\n"
                        response += f"   {result['snippet']}\n"
                        response += f"   🔗 {result['url']}\n\n"
                    
                    send_to_chatwoot(conv_id, response)
                else:
                    send_to_chatwoot(conv_id, f"❌ '{query}' хайлтаар илэрц олдсонгүй.")

    elif text.lower() in ["help", "тусламж"]:
        help_text = f"""
👋 Сайн байна уу {contact_name}! Би Cloud.mn-ийн AI туслах юм.

🤖 **Боломжит командууд:**
• `crawl` - Бүх сайтыг шүүрдэх
• `scrape <URL>` - Тодорхой хуудас шүүрдэх
• `search <асуулт>` - Мэдээлэл хайх
• `help` - Энэ тусламжийг харуулах

💬 **Чөлөөт ярилцлага:**
Та мөн надад асуулт асууж, ярилцаж болно. Би монгол хэлээр хариулна.

⏰ Үргэлж тусламжид бэлэн байна!
        """
        send_to_chatwoot(conv_id, help_text)

    elif text.lower() in ["баяртай", "goodbye", "баай"]:
        send_to_chatwoot(conv_id, f"👋 Баяртай {contact_name}! Дараа уулзацгаая!")
        mark_conversation_resolved(conv_id)

    else:
        # General AI conversation
        send_to_chatwoot(conv_id, "🤔 Боловсруулж байна...")
        ai_response = get_ai_response(text, conv_id, crawled_data)
        send_to_chatwoot(conv_id, ai_response)

    return jsonify({"status": "success"}), 200


# —— Additional API Endpoints —— #
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
    return jsonify({"pages": len(crawled_data), "data": crawled_data[:10]})  # First 10 pages

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "crawled_pages": len(crawled_data),
        "active_conversations": len(conversation_memory)
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
