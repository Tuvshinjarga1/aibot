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
    """Enhanced content extraction with better text processing for Mongolian responses"""
    # Try to find main content areas in order of preference
    main = (soup.find("main") or 
            soup.find("article") or 
            soup.find("div", class_=["content", "main-content", "post-content", "documentation", "docs"]) or 
            soup.find("div", id=["content", "main", "docs"]) or
            soup)
    
    texts = []
    images = []

    # Remove unwanted elements that don't contain useful content
    for element in main.find_all(['script', 'style', 'nav', 'header', 'footer', 'aside', 'menu']):
        element.decompose()

    # Extract headings with hierarchy for better structure
    for tag in main.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 2:  # Filter out very short headings
            level = int(tag.name[1])
            indent = "  " * (level - 1)
            texts.append(f"{indent}• {text}")

    # Extract paragraphs and list items with better filtering
    for tag in main.find_all(["p", "li", "blockquote", "div"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 15:  # Filter out very short content
            # Clean up excessive whitespace
            text = ' '.join(text.split())
            # Skip if it's mostly navigation or metadata
            if not any(nav_word in text.lower() for nav_word in ['home', 'menu', 'navigation', 'breadcrumb', 'skip to']):
                texts.append(text)

    # Extract code blocks with context
    for tag in main.find_all(["code", "pre"]):
        code_text = tag.get_text(strip=True)
        if code_text and len(code_text) > 5:
            # Add context about what kind of code this is
            parent_text = ""
            parent = tag.find_parent(['p', 'div', 'section'])
            if parent:
                prev_text = parent.get_text(strip=True)[:100]
                if prev_text and prev_text != code_text:
                    parent_text = f" ({prev_text}...)"
            texts.append(f"[Кодын жишээ{parent_text}] {code_text}")

    # Extract table data with structure
    for table in main.find_all("table"):
        headers = []
        rows = []
        
        # Get headers if available
        header_row = table.find("tr")
        if header_row:
            headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
        
        # Get data rows
        for row in table.find_all("tr")[1:]:  # Skip header row
            cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
            if any(cells):  # Only add non-empty rows
                rows.append(" | ".join(cells))
        
        if rows:
            table_text = "[Хүснэгт]\n"
            if headers:
                table_text += " | ".join(headers) + "\n" + "-" * 40 + "\n"
            table_text += "\n".join(rows)
            texts.append(table_text)

    # Extract images with better descriptions
    for img in main.find_all("img"):
        src = img.get("src")
        alt = img.get("alt", "").strip()
        title = img.get("title", "").strip()
        if src:
            full_img_url = urljoin(base_url, src)
            description = alt or title or "зураг"
            # Add context from surrounding text
            parent = img.find_parent(['p', 'div', 'figure'])
            context = ""
            if parent:
                parent_text = parent.get_text(strip=True)[:100]
                if parent_text and description not in parent_text:
                    context = f" - {parent_text}"
            entry = f"[Зураг: {description}{context}] {full_img_url}"
            texts.append(entry)
            images.append({"url": full_img_url, "alt": description})

    # Extract important links with context
    for link in main.find_all("a", href=True):
        link_text = link.get_text(strip=True)
        href = link.get("href")
        if link_text and len(link_text) > 5 and not href.startswith("#"):
            full_url = urljoin(base_url, href)
            # Only include external links or important internal ones
            if (not full_url.startswith(base_url) or 
                any(important in href.lower() for important in ['api', 'doc', 'guide', 'tutorial', 'example'])):
                texts.append(f"[Холбоос: {link_text}] {full_url}")

    # Extract definition lists (dl, dt, dd) which are common in documentation
    for dl in main.find_all("dl"):
        terms = dl.find_all("dt")
        descriptions = dl.find_all("dd")
        for term, desc in zip(terms, descriptions):
            term_text = term.get_text(strip=True)
            desc_text = desc.get_text(strip=True)
            if term_text and desc_text:
                texts.append(f"[Тодорхойлолт] {term_text}: {desc_text}")

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
    """Enhanced AI response with better context awareness and detailed Mongolian responses"""
    
    if not client:
        return "🔑 OpenAI API түлхүүр тохируулагдаагүй байна. Админтай холбогдоно уу."
    
    # Get conversation history
    history = conversation_memory.get(conversation_id, [])
    
    # Build context from crawled data if available
    context = ""
    search_results = []
    if context_data and crawled_data:
        # Search for relevant content with more results for better context
        search_results = search_in_crawled_data(user_message, max_results=5)
        if search_results:
            relevant_pages = []
            for result in search_results:
                relevant_pages.append(
                    f"===== {result['title']} =====\n"
                    f"URL: {result['url']}\n"
                    f"Агуулга: {result['snippet']}\n"
                    f"Холбогдох оноо: {result['relevance_score']}\n"
                )
            context = "\n".join(relevant_pages)
    
    # Enhanced system message with better Mongolian context
    system_content = """Та Cloud.mn-ийн облачын үйлчилгээний талаар асуултад хариулдаг мэргэжлийн Монгол AI туслах юм. 

ЧУХАЛ ЗААВАРЧИЛГАА:
1. Монгол хэлээр л хариулна - бүх хариултаа монгол үсэг, үгээр бичнэ үү
2. Найрсаг, тусламж дэмжлэгийн үйлчилгээ үзүүлнэ
3. Техникийн нэр томъёог монголоор ойлгомжтой тайлбарлана
4. Хариултаа бүтэцтэй, дэлгэрэнгүй, практик байлгана
5. Баримт бичгийн холбоосуудыг оруулж, эх сурвалжтай хариулна

ХАРИУЛАХ ЗАГВАР:
- Эхлээд товч хариулт өгөөд дэлгэрэнгүй тайлбар нэмнэ
- Бодит жишээ болон практик зөвлөмж өгнө
- Холбогдох материалын холбоосуудыг жагсаана
- Шаардлагатай бол алхам бүрчлэн заавар өгнө
- Эцэст нь нэмэлт тусламжийн санал өгнө

ТАНЫ МЭРГЭЖЛИЙН ЧИГЛЭЛ:
- Cloud.mn-ийн облачын үйлчилгээ (хостинг, сервер, домайн)
- Баз өгөгдөл, хадгалах сан
- Үнийн мэдээлэл, багц үйлчилгээ
- Техникийн дэмжлэг, troubleshooting
- Аюулгүй байдал, backup

Хэрэглэгчийн асуултад холбогдох мэдээллийг доорх баримт бичгээс ашиглан дэлгэрэнгүй, туслах зорилготой хариулаарай."""
    
    if context:
        system_content += f"\n\nХОЛБОГДОХ БАРИМТ БИЧГИЙН МЭДЭЭЛЭЛ:\n{context}"
        system_content += f"\n\nЭдгээр мэдээллийг ашиглан асуултад хариулаарай. Заавал холбоосуудыг дурдаарай."
    
    # Build conversation context
    messages = [
        {
            "role": "system", 
            "content": system_content
        }
    ]
    
    # Add conversation history (last 6 messages for better context)
    for msg in history[-6:]:
        messages.append(msg)
    
    # Add current message
    messages.append({"role": "user", "content": user_message})
    
    try:
        response = client.chat.completions.create(
            model="gpt-4",  # Changed from gpt-4.1 to gpt-4 for better reliability
            messages=messages,
            max_tokens=800,  # Increased for more detailed responses
            temperature=0.3,  # Lower temperature for more consistent responses
            presence_penalty=0.1,
            frequency_penalty=0.1
        )
        
        ai_response = response.choices[0].message.content
        
        # Post-process response to ensure Mongolian quality and add helpful formatting
        if ai_response:
            # Add helpful formatting if missing and we have search results
            if context and "🔗" not in ai_response and search_results:
                ai_response += f"\n\n📚 **Холбогдох материал:**"
                for result in search_results[:3]:
                    ai_response += f"\n• {result['title']}: {result['url']}"
        
        # Store in memory
        if conversation_id not in conversation_memory:
            conversation_memory[conversation_id] = []
        
        conversation_memory[conversation_id].append({"role": "user", "content": user_message})
        conversation_memory[conversation_id].append({"role": "assistant", "content": ai_response})
        
        # Keep only last 10 messages for better context retention
        if len(conversation_memory[conversation_id]) > 10:
            conversation_memory[conversation_id] = conversation_memory[conversation_id][-10:]
            
        return ai_response
        
    except Exception as e:
        logging.error(f"OpenAI API алдаа: {e}")
        return f"""🔧 AI-тай холбогдоход саад гарлаа. 

🔍 **Одоогийн төлөв:** {crawl_status.get('message', 'Мэдээгүй төлөв')}

⚠️ **Алдааны дэлгэрэнгүй:** {str(e)[:150]}..."""

def search_in_crawled_data(query: str, max_results: int = 5):
    """Enhanced search with better Mongolian text processing and relevance scoring"""
    if not crawled_data:
        return []
    
    query_lower = query.lower()
    query_words = query_lower.split()
    results = []
    scored_pages = []
    
    for page in crawled_data:
        score = 0
        title = page['title'].lower()
        body = page['body'].lower()
        url = page['url'].lower()
        
        # Title matches (highest priority)
        if query_lower in title:
            score += 10
        else:
            title_word_matches = sum(1 for word in query_words if word in title)
            score += title_word_matches * 3
            
        # URL matches (good indicator)
        if any(word in url for word in query_words):
            score += 2
            
        # Body content matches
        if query_lower in body:
            score += 5
            # Bonus for multiple occurrences
            score += body.count(query_lower) * 0.5
        else:
            body_word_matches = sum(1 for word in query_words if word in body)
            score += body_word_matches
            
        # Exact phrase matches (very high priority)
        if f'"{query_lower}"' in body or f"'{query_lower}'" in body:
            score += 15
            
        # Proximity bonus: words appearing close together
        if len(query_words) > 1:
            for i, word1 in enumerate(query_words):
                for word2 in query_words[i+1:]:
                    if word1 in body and word2 in body:
                        pos1 = body.find(word1)
                        pos2 = body.find(word2)
                        if abs(pos1 - pos2) < 100:  # Words within 100 characters
                            score += 2
            
        # Length and quality bonus
        if len(page['body']) > 200:  # Substantial content
            score += 1
            
        if score > 0:
            scored_pages.append((score, page))
    
    # Sort by score and get top results
    scored_pages.sort(key=lambda x: x[0], reverse=True)
    
    for score, page in scored_pages[:max_results]:
        # Find the most relevant snippet with better context
        body = page['body']
        best_snippet = ""
        max_context = 400
        
        # Try to find context around query words
        snippet_candidates = []
        
        for word in query_words:
            word_pos = body.lower().find(word)
            if word_pos != -1:
                start = max(0, word_pos - 150)
                end = min(len(body), word_pos + 250)
                candidate = body[start:end].strip()
                
                # Try to break at sentence boundaries
                sentences = candidate.split('.')
                if len(sentences) > 2:
                    candidate = '.'.join(sentences[1:-1]) + '.'
                
                snippet_candidates.append((len(candidate), candidate))
        
        # Choose the longest meaningful snippet
        if snippet_candidates:
            snippet_candidates.sort(key=lambda x: x[0], reverse=True)
            best_snippet = snippet_candidates[0][1]
        
        # Fallback to beginning of content
        if not best_snippet or len(best_snippet) < 50:
            best_snippet = body[:max_context]
            if len(body) > max_context:
                # Try to end at a sentence
                last_period = best_snippet.rfind('.')
                if last_period > max_context * 0.7:
                    best_snippet = best_snippet[:last_period + 1]
                else:
                    best_snippet += "..."
        
        # Clean up the snippet
        best_snippet = ' '.join(best_snippet.split())  # Normalize whitespace
            
        results.append({
            'title': page['title'],
            'url': page['url'],
            'snippet': best_snippet,
            'relevance_score': round(score, 1)
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
    global crawled_data, crawl_status  # Move global declaration to the top
    
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
            send_to_chatwoot(conv_id, 
                f"✅ Сайт аль хэдийн шүүрдэгдсэн байна! "
                f"{crawl_status.get('pages_count', 0)} хуудас бэлэн.\n\n"
            )
        elif crawl_status["status"] == "running":
            send_to_chatwoot(conv_id, "🔄 Сайт одоо шүүрдэгдэж байна. Түр хүлээнэ үү...")
        else:
            send_to_chatwoot(conv_id, f"🔄 Сайн байна уу {contact_name}! Сайтыг шүүрдэж байна...")
            
            crawl_status = {"status": "running", "message": f"Manual crawl started by {contact_name}"}
            crawled_data = crawl_and_scrape(ROOT_URL)
            
            if not crawled_data:
                crawl_status = {"status": "failed", "message": "Manual crawl failed"}
                send_to_chatwoot(conv_id, "❌ Шүүрдэх явцад алдаа гарлаа. Дахин оролдоно уу.")
            else:
                crawl_status = {
                    "status": "completed", 
                    "message": f"Manual crawl completed by {contact_name}",
                    "pages_count": len(crawled_data),
                    "timestamp": datetime.now().isoformat()
                }
                lines = [f"📄 {p['title']} — {p['url']}" for p in crawled_data[:3]]
                send_to_chatwoot(conv_id,
                    f"✅ {len(crawled_data)} хуудас амжилттай шүүрдлээ!\n\n"
                    f"Эхний 3 хуудас:\n" + "\n".join(lines) + 
                    f"\n\nОдоо танд Cloud.mn-ийн талаар асуулт асууж болно!"
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
            
            # Check crawl status first
            if crawl_status["status"] == "running":
                send_to_chatwoot(conv_id, "🔄 Сайт шүүрдэгдэж байна. Түр хүлээгээд дахин оролдоно уу.")
            elif crawl_status["status"] in ["not_started", "failed", "error"] or not crawled_data:
                send_to_chatwoot(conv_id, 
                    "📚 Мэдээлэл бэлэн байхгүй байна. 'crawl' командыг ашиглан сайтыг шүүрдүүлнэ үү."
                )
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
                else:
                    send_to_chatwoot(conv_id, f"❌ '{query}' хайлтаар илэрц олдсонгүй.")

    elif text.lower() in ["help", "тусламж"]:
        # Show status-aware help focused on conversation
        status_info = ""
        if crawl_status["status"] == "completed":
            status_info = f"✅ {crawl_status.get('pages_count', 0)} хуудасны мэдээлэл бэлэн байна.\n"
        elif crawl_status["status"] == "running":
            status_info = "🔄 Сайтын мэдээлэл цуглуулагдаж байна.\n"
        elif crawl_status["status"] == "disabled":
            status_info = "⚠️ Автомат мэдээлэл цуглуулах идэвхгүй байна.\n"
        
        help_text = f"""👋 Сайн байна уу {contact_name}! 

🤖 **Би Cloud.mn-ийн AI туслах юм:**
• Cloud.mn-ийн үйлчилгээний талаар асуулт асуугаарай
• Техникийн асуултад монгол хэлээр хариулна
• Облачын үйлчилгээ, API, системийн талаар мэдээлэл өгнө

📊 **Одоогийн төлөв:**
{status_info}

💬 **Жишээ асуултууд:**
• "Облачын үйлчилгээний талаар хэлээрэй"
• "API хэрхэн ашиглах вэ?"
• "Баз өгөгдлийн тохиргоо хэрхэн хийх вэ?"
• "Үнийн талаар мэдээлэл өгөөрөй"

🎯 **Миний чадвар:**
• Дэлгэрэнгүй тайлбар өгөх
• Алхам бүрчлэн заавар өгөх  
• Холбогдох материалын холбоос өгөх
• Монгол хэлээр бүх асуултад хариулах

⏰ **Та надтай чөлөөтэй ярилцаж болно!**"""
        
        send_to_chatwoot(conv_id, help_text)

    elif text.lower() in ["баяртай", "goodbye", "баай"]:
        send_to_chatwoot(conv_id, f"👋 Баяртай {contact_name}! Дараа уулзацгаая!")
        mark_conversation_resolved(conv_id)

    else:
        # Enhanced General AI conversation with better context
        send_to_chatwoot(conv_id, "🤔 Боловсруулж байна...")
        
        # Check if we have crawled data to provide context
        if crawled_data and crawl_status["status"] == "completed":
            # For general conversation, use crawled data as context
            ai_response = get_ai_response(text, conv_id, crawled_data)
        else:
            # If no data available, inform user and provide basic response
            basic_response = f"""Сайн байна уу {contact_name}! 

🤖 Би Cloud.mn-ийн AI туслах юм. Танд хэрхэн туслах вэ?

⚠️ **Анхаарна уу:** Одоогоор баримт бичгийн мэдээлэл бэлэн байхгүй байна.

💡 **Зөвлөмж:** 
• Ерөнхий асуултад хариулж чадна
• Илүү дэлгэрэнгүй мэдээлэл авахын тулд администратортай холбогдоно уу

🔧 **Техникийн дэмжлэг:** admin@cloud.mn"""
            
            # Try to get AI response anyway for general questions
            try:
                ai_response = get_ai_response(text, conv_id, None)
                # Add disclaimer if no context available
                if "OpenAI API" not in ai_response and "алдаа" not in ai_response:
                    ai_response += f"\n\n⚠️ *Энэ хариулт ерөнхий мэдлэг дээр суурилсан бөгөөд Cloud.mn-ийн тодорхой баримт бичигт суурилаагүй байна.*"
            except:
                ai_response = basic_response
        
        send_to_chatwoot(conv_id, ai_response)

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
