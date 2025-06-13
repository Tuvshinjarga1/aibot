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
    
    Та хэрэглэгчийн асуултад шууд хариулж, тусламж үзүүлээрэй. Ямар нэгэн тусгай команд шаардахгүй."""
    
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
        return f"🔧 AI-тай холбогдоход саад гарлаа. Дахин оролдоно уу эсвэл админтай холбогдоно уу.\n\nАлдааны дэлгэрэнгүй: {str(e)[:100]}"

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

def assign_to_support_team(conv_id: int, escalation_reason: str):
    """Assign conversation to support team and add labels"""
    
    # Add label to conversation
    label_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/labels"
    headers = {"api_access_token": CHATWOOT_API_KEY, "Content-Type": "application/json"}
    
    # Add escalation label
    label_payload = {"labels": ["escalated", "needs-human-support"]}
    
    try:
        resp = requests.post(label_url, json=label_payload, headers=headers, timeout=10)
        if resp.status_code in [200, 201]:
            logging.info(f"Added escalation labels to conversation {conv_id}")
        
        # Update conversation status to open and priority
        status_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
        status_payload = {
            "status": "open",
            "priority": "high",
            "custom_attributes": {
                "escalation_reason": escalation_reason,
                "escalated_at": datetime.now().isoformat()
            }
        }
        
        resp = requests.patch(status_url, json=status_payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            logging.info(f"Updated conversation {conv_id} status for escalation")
            return True
            
    except Exception as e:
        logging.error(f"Failed to assign conversation to support team: {e}")
    
    return False


# —— Microsoft Teams Integration —— #
def send_to_teams(user_message: str, contact_name: str = "Хэрэглэгч", conv_id: int = None):
    """Send user question to Microsoft Teams"""
    if not TEAMS_WEBHOOK_URL:
        logging.warning("Teams webhook URL not configured")
        return False
    
    # Create Teams message card
    teams_message = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0076D7",
        "summary": "Шинэ асуулт Cloud.mn-ээс",
        "sections": [{
            "activityTitle": "🤖 Cloud.mn AI Assistant",
            "activitySubtitle": f"Хэрэглэгчийн асуулт - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "facts": [
                {
                    "name": "Хэрэглэгч:",
                    "value": contact_name
                },
                {
                    "name": "Харилцан яриа ID:",
                    "value": str(conv_id) if conv_id else "Тодорхойгүй"
                },
                {
                    "name": "Асуулт:",
                    "value": user_message[:500] + "..." if len(user_message) > 500 else user_message
                }
            ],
            "markdown": True
        }]
    }
    
    try:
        headers = {"Content-Type": "application/json"}
        resp = requests.post(TEAMS_WEBHOOK_URL, json=teams_message, headers=headers, timeout=10)
        resp.raise_for_status()
        logging.info(f"Message sent to Teams for conversation {conv_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to send message to Teams: {e}")
        return False

def send_escalation_to_teams(user_message: str, contact_name: str, conv_id: int, reason: str):
    """Send escalation notification to Teams support team"""
    if not TEAMS_WEBHOOK_URL:
        logging.warning("Teams webhook URL not configured")
        return False
    
    # Create escalation message card
    teams_message = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "FF6B35",  # Orange color for escalation
        "summary": "🚨 Дэмжлэгийн багт чиглүүлэх асуулт",
        "sections": [{
            "activityTitle": "🚨 ДЭМЖЛЭГИЙН БАГТ ЧИГЛҮҮЛЭХ",
            "activitySubtitle": f"AI-аас хүний дэмжлэг шаардлагатай - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "facts": [
                {
                    "name": "Хэрэглэгч:",
                    "value": contact_name
                },
                {
                    "name": "Харилцан яриа ID:",
                    "value": str(conv_id)
                },
                {
                    "name": "Чиглүүлэх шалтгаан:",
                    "value": reason
                },
                {
                    "name": "Асуулт:",
                    "value": user_message[:400] + "..." if len(user_message) > 400 else user_message
                }
            ],
            "markdown": True
        }],
        "potentialAction": [{
            "@type": "OpenUri",
            "name": "Chatwoot-д харах",
            "targets": [{
                "os": "default",
                "uri": f"{CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
            }]
        }]
    }
    
    try:
        headers = {"Content-Type": "application/json"}
        resp = requests.post(TEAMS_WEBHOOK_URL, json=teams_message, headers=headers, timeout=10)
        resp.raise_for_status()
        logging.info(f"Escalation sent to Teams for conversation {conv_id}: {reason}")
        return True
    except Exception as e:
        logging.error(f"Failed to send escalation to Teams: {e}")
        return False

def analyze_need_human_support(user_message: str, ai_response: str = None) -> tuple[bool, str]:
    """Analyze if the question needs human support based on content and AI confidence"""
    
    # Keywords that typically require human support
    escalation_keywords = [
        # Technical issues
        'алдаа', 'error', 'bug', 'асуудал', 'problem', 'issue',
        'ажиллахгүй', 'not working', 'broken', 'эвдэрсэн',
        
        # Account/billing related
        'төлбөр', 'billing', 'payment', 'данс', 'account', 'subscription',
        'цуцлах', 'cancel', 'refund', 'буцаах',
        
        # Urgent requests
        'яаралтай', 'urgent', 'асуудалтай', 'тусламж', 'help me',
        'холбогдох', 'contact', 'дуудах', 'call',
        
        # Complex technical setup
        'суулгах', 'install', 'тохируулах', 'configure', 'setup',
        'интеграци', 'integration', 'api', 'webhook',
        
        # Complaints
        'гомдол', 'complaint', 'сэтгэл хангалуун бус', 'dissatisfied'
    ]
    
    # Check for escalation keywords
    message_lower = user_message.lower()
    found_keywords = [kw for kw in escalation_keywords if kw in message_lower]
    
    # Check message length - very long messages might need human attention
    is_complex = len(user_message) > 300
    
    # Check for question marks - multiple questions might be complex
    question_count = user_message.count('?') + user_message.count('уу')
    is_multi_question = question_count > 2
    
    # Check AI response confidence indicators
    low_confidence_phrases = [
        'мэдэхгүй', 'тодорхойгүй', 'баталж чадахгүй', 'админтай холбогдоно уу',
        'дэлгэрэнгүй мэдээлэл', 'нэмэлт тусламж'
    ]
    
    ai_uncertain = False
    if ai_response:
        ai_response_lower = ai_response.lower()
        ai_uncertain = any(phrase in ai_response_lower for phrase in low_confidence_phrases)
    
    # Decision logic
    if found_keywords:
        reason = f"Техникийн дэмжлэг шаардлагатай түлхүүр үгс: {', '.join(found_keywords[:3])}"
        return True, reason
    
    if ai_uncertain:
        reason = "AI хариулт тодорхойгүй байна"
        return True, reason
    
    if is_complex and is_multi_question:
        reason = "Нарийн төвөгтэй олон асуулттай"
        return True, reason
    
    # Check for direct requests to talk to human
    human_request_phrases = ['хүнтэй ярих', 'дэмжлэгийн баг', 'support team', 'админ']
    if any(phrase in message_lower for phrase in human_request_phrases):
        reason = "Хэрэглэгч шууд хүний дэмжлэг хүссэн"
        return True, reason
    
    return False, ""


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
    time.sleep(10)
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
    
    # Send user question to Microsoft Teams
    send_to_teams(text, contact_name, conv_id)
    
    # General AI conversation only
    ai_response = get_ai_response(text, conv_id, crawled_data)
    
    # Analyze if human support is needed
    needs_escalation, escalation_reason = analyze_need_human_support(text, ai_response)
    
    if needs_escalation:
        # Send escalation notification to Teams
        send_escalation_to_teams(text, contact_name, conv_id, escalation_reason)
        
        # Assign conversation to support team in Chatwoot
        assign_to_support_team(conv_id, escalation_reason)
        
        # Add escalation notice to AI response
        escalation_notice = f"\n\n🚨 **Дэмжлэгийн багт чиглүүлэх шаардлагатай**\nШалтгаан: {escalation_reason}\n\nМанай дэмжлэгийн баг удахгүй танд хариулах болно."
        ai_response += escalation_notice
        
        # Mark conversation for human attention (you can add tags or labels here)
        logging.info(f"Conversation {conv_id} escalated to support team: {escalation_reason}")
    
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
            "chatwoot_configured": bool(CHATWOOT_API_KEY and ACCOUNT_ID),
            "teams_configured": bool(TEAMS_WEBHOOK_URL)
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
