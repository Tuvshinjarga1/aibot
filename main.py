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
import re

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

# AI Context and Decision System
class AIContext:
    """Unified AI context management for flexible workflow"""
    def __init__(self, conv_id: int, user_message: str):
        self.conv_id = conv_id
        self.user_message = user_message
        self.conversation_history = conversation_memory.get(conv_id, [])
        self.search_results = None
        self.ai_response = None
        self.analysis_result = None
        self.suggested_services = None
        
    def get_full_context(self):
        """Get complete conversation context"""
        user_messages = []
        ai_messages = []
        for msg in self.conversation_history:
            if msg.get("role") == "user":
                user_messages.append(msg.get("content", ""))
            elif msg.get("role") == "assistant":
                ai_messages.append(msg.get("content", ""))
        
        # Add current message
        if self.user_message not in user_messages:
            user_messages.append(self.user_message)
            
        return {
            "user_messages": user_messages,
            "ai_messages": ai_messages,
            "full_conversation": "\n".join(user_messages),
            "conversation_length": len(self.conversation_history)
        }
    
    def should_search_docs(self):
        """AI decides if documentation search is needed"""
        if not client or not crawled_data:
            return False
            
        try:
            context = self.get_full_context()
            prompt = f"""
Хэрэглэгчийн асуулт: {self.user_message}
Харилцлагын контекст: {context['full_conversation'][-500:]}

Энэ асуултад хариулахын тулд баримт бичгээс хайлт хийх шаардлагатай юу?

'yes' эсвэл 'no' гэж хариулна уу.
            """
            
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "Та хайлтын шаардлагыг тодорхойлдог мэргэжилтэн."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=10,
                temperature=0.2
            )
            
            return response.choices[0].message.content.strip().lower() == "yes"
        except:
            # Fallback: search if message contains technical terms
            tech_indicators = ["хэрхэн", "яаж", "install", "setup", "config", "тохируулах", "суулгах"]
            return any(indicator in self.user_message.lower() for indicator in tech_indicators)

def get_enhanced_ai_response(ai_context: AIContext):
    """Enhanced conversational AI that prioritizes docs search but falls back intelligently"""
    
    if not client:
        return "🔑 OpenAI API түлхүүр тохируулагдаагүй байна. Админтай холбогдоно уу."
    
    context_info = ai_context.get_full_context()
    
    # Step 1: Smart document search with enhanced accuracy
    doc_context = ""
    search_results = []
    found_in_docs = False
    
    if crawled_data:
        search_results = search_in_crawled_data(ai_context.user_message, max_results=5)
        
        # Enhanced filtering - only use high precision results
        high_precision_results = [r for r in search_results if r.get('precision_level') == 'high']
        
        if high_precision_results:
            found_in_docs = True
            ai_context.search_results = high_precision_results
            
            relevant_pages = []
            for result in high_precision_results:
                page = next((p for p in crawled_data if p['url'] == result['url']), None)
                if page:
                    image_info = ""
                    if page.get('images'):
                        image_info = "\nЗургууд:\n" + "\n".join([
                            f"- {img['alt']}: {img['url']}" if img['alt'] else f"- {img['url']}"
                            for img in page['images'][:3]  # Limit to 3 images
                        ])
                    
                    relevant_pages.append(
                        f"📄 {result['title']}\n"
                        f"🔗 {result['url']}\n"
                        f"📝 {result['snippet']}\n"
                        f"🎯 Нарийвчлал: {result['relevance_score']}/15\n"
                        f"{image_info}\n"
                    )
            doc_context = "\n".join(relevant_pages)
    
    # Step 2: Adaptive system prompt based on search results
    if found_in_docs:
        personality = """Та Cloud.mn-ийн баримт бичгийн мэргэжлийн туслах. 
        Баримт бичгийн мэдээллийг ашиглан МАШ НАРИЙВЧЛАЛТАЙ, ТОВЧ, ТОДОРХОЙ хариулт өгнө.
        Хариултаа бүтэцтэй байлгаж, хэрэглэгчид шууд хэрэглэж болох зааварчилгаа өгнө."""
    else:
        personality = """Та Cloud.mn-ийн ухаалаг AI туслах. Баримт бичигт олдохгүй асуултуудад 
        өөрийн мэдлэгээрээ хариулж, шаардлагатай бол дэмжлэгийн багт уламжлана."""
    
    system_content = f"""{personality}
    
ҮНДСЭН ЗАРЧИМ: Монгол хэлээр тодорхой, практик хариулт өгнө.

Одоогийн нөхцөл байдал:
- Баримт бичгээс олдсон: {'Тийм' if found_in_docs else 'Үгүй'}
- Харилцлагын түүх: {context_info["conversation_length"]} мессэж
- Контекстын хэмжээ: {len(context_info['full_conversation'])} тэмдэгт

ХАРИУЛТЫН ЗАГВАР:
1. Хариултыг {200 if found_in_docs else 150} үгээс багагүй байлгах
2. Практик алхам алхмаар зааварчилгаа өгөх  
3. Холбогдох линк болон дэмжлэг санал болгох
4. Техникийн нэр томъёог монгол хэлээр тайлбарлах

{'БАРИМТ БИЧГИЙН МЭДЭЭЛЭЛ:\n' + doc_context if doc_context else ''}
    """
    
    # Step 3: Build conversation with smart context management
    messages = [{"role": "system", "content": system_content}]
    
    # Add relevant conversation history
    history_limit = 3 if found_in_docs else 5
    recent_messages = []
    for msg in context_info["user_messages"][-history_limit:]:
        if msg != ai_context.user_message:
            recent_messages.append({"role": "user", "content": msg})
    
    messages.extend(recent_messages)
    messages.append({"role": "user", "content": ai_context.user_message})
    
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            max_tokens=800 if found_in_docs else 600,
            temperature=0.4 if found_in_docs else 0.6
        )
        
        ai_response = response.choices[0].message.content
        ai_context.ai_response = ai_response
        ai_context.found_in_docs = found_in_docs
        
        # Store in memory
        if ai_context.conv_id not in conversation_memory:
            conversation_memory[ai_context.conv_id] = []
        
        conversation_memory[ai_context.conv_id].append({"role": "user", "content": ai_context.user_message})
        conversation_memory[ai_context.conv_id].append({"role": "assistant", "content": ai_response})
        
        # Keep only last 10 messages for better context
        if len(conversation_memory[ai_context.conv_id]) > 10:
            conversation_memory[ai_context.conv_id] = conversation_memory[ai_context.conv_id][-10:]
            
        return ai_response
        
    except Exception as e:
        logging.error(f"Enhanced AI response failed: {e}")
        return f"🔧 AI системтэй холбогдоход алдаа гарлаа.\n\nТа дараах аргуудаар тусламж авч болно:\n• Асуултыг дахин тодорхой асуух\n• 'тусламж' гэж бичих\n\nАлдааны мэдээлэл: {str(e)[:100]}"

def send_enhanced_teams_notification(ai_context: AIContext, problems_analysis: dict):
    """Enhanced Teams notification with detailed problem breakdown - focus on problems"""
    if not TEAMS_WEBHOOK_URL or not problems_analysis:
        return False
        
    try:
        conv_info = get_conversation_info(ai_context.conv_id)
        if not conv_info:
            return False
            
        contact = conv_info.get("contact", {})
        contact_name = contact.get("name", "Хэрэглэгч")
        contact_email = contact.get("email", "Имэйл олдсонгүй")
        
        overall = problems_analysis.get("overall_analysis", {})
        problems = problems_analysis.get("problems", [])
        
        # Create focused problem descriptions
        problems_text = ""
        for i, problem in enumerate(problems, 1):
            priority_emoji = "🔴" if problem['priority'] == 'high' else "🟡" if problem['priority'] == 'medium' else "🟢"
            category_emoji = "⚙️" if problem['category'] == 'technical' else "💼" if problem['category'] == 'service_request' else "❓"
            complexity_emoji = "🔥" if problem['complexity'] == 'complex' else "⚡" if problem['complexity'] == 'moderate' else "✅"
            
            problems_text += f"""
{priority_emoji} **Асуудал {i}:** {problem['title']}
{category_emoji} **Төрөл:** {problem['category']}
{complexity_emoji} **Төвөгтэй байдал:** {problem['complexity']}
📋 **Дэлгэрэнгүй:** {problem['description']}
"""
        
        # Get only the most relevant conversation context (last user message + key context)
        context_info = ai_context.get_full_context()
        latest_user_message = ai_context.user_message
        
        # Create a concise summary instead of full conversation
        conversation_summary = ""
        if len(context_info['user_messages']) > 1:
            conversation_summary = f"• Өмнөх асуултууд: {len(context_info['user_messages']) - 1}\n"
        conversation_summary += f"• Одоогийн асуулт: {latest_user_message[:200]}{'...' if len(latest_user_message) > 200 else ''}"
        
        # Create streamlined Teams message focused on problems
        teams_message = f"""
🚨 **ТЕХНИКИЙН ДЭМЖЛЭГ ШААРДЛАГАТАЙ**

👤 **Хэрэглэгч:** {contact_name}
📧 **Имэйл:** {contact_email}
🆔 **Conversation ID:** {ai_context.conv_id}

📊 **Ерөнхий үнэлгээ:**
• Чухал байдал: {'🔴 Өндөр' if overall.get('is_critical') else '🟡 Дунд'}
• Итгэлийн түвшин: {overall.get('confidence', 0)}%
• Баримт бичигт олдсон: {'❌ Үгүй' if not overall.get('found_in_docs') else '✅ Тийм'}

🔍 **Илрүүлсэн асуудлууд ({len(problems)}):**
{problems_text}

💬 **Харилцлагын хураангуй:**
{conversation_summary}

🤖 **AI дүгнэлт:**
{ai_context.ai_response[:250] if ai_context.ai_response else 'AI хариулт байхгүй'}{'...' if ai_context.ai_response and len(ai_context.ai_response) > 250 else ''}

🕒 **Огноо:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

🔗 **Chatwoot линк:** {CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{ai_context.conv_id}
        """
        
        # Determine color based on priority
        color = "FF0000" if overall.get('is_critical') else "FF6B35"  # Red for critical, Orange for normal
        
        return send_to_teams(
            message=teams_message,
            title=f"🚨 {contact_name} - {len(problems)} асуудал илэрлээ",
            color=color,
            conv_id=ai_context.conv_id
        )
        
    except Exception as e:
        logging.error(f"Enhanced Teams notification failed: {e}")
        return False

# Legacy function for backward compatibility
def get_ai_response(user_message: str, conversation_id: int, context_data: list = None):
    """Legacy wrapper for backward compatibility"""
    ai_context = AIContext(conversation_id, user_message)
    return get_enhanced_ai_response(ai_context)

def search_in_crawled_data(query: str, max_results: int = 5):
    """Enhanced search with higher precision and better relevance scoring"""
    if not crawled_data:
        return []
    
    query_lower = query.lower()
    results = []
    scored_pages = []
    
    # Enhanced keyword extraction for better matching
    query_words = [w.strip() for w in query_lower.split() if len(w.strip()) > 2]
    
    for page in crawled_data:
        score = 0
        title = page['title'].lower()
        body = page['body'].lower()
        
        # Exact phrase matching (highest priority)
        if query_lower in body:
            score += 10
        if query_lower in title:
            score += 15
            
        # Title keyword matches (high priority)
        title_matches = sum(1 for word in query_words if word in title)
        score += title_matches * 5
        
        # Body keyword matches
        body_matches = sum(1 for word in query_words if word in body)
        score += body_matches * 2
        
        # Bonus for multiple word matches in close proximity
        if len(query_words) > 1:
            for i, word1 in enumerate(query_words):
                for word2 in query_words[i+1:]:
                    if word1 in body and word2 in body:
                        # Check if words are close together (within 50 characters)
                        pos1 = body.find(word1)
                        pos2 = body.find(word2)
                        if abs(pos1 - pos2) < 50:
                            score += 3
        
        # Technical terms bonus
        tech_terms = ['config', 'setup', 'install', 'тохируулах', 'суулгах', 'server', 'сервер']
        if any(term in query_lower for term in tech_terms) and any(term in body for term in tech_terms):
            score += 3
            
        if score > 0:
            scored_pages.append((score, page))
    
    # Sort by score and get top results
    scored_pages.sort(key=lambda x: x[0], reverse=True)
    
    for score, page in scored_pages[:max_results]:
        # Find the most relevant snippet with better context
        body = page['body']
        best_snippet = ""
        max_context = 400
        
        # Try to find snippet containing multiple query words
        best_match_pos = -1
        max_word_matches = 0
        
        for i in range(0, len(body) - 100, 50):
            snippet_part = body[i:i+200].lower()
            word_matches = sum(1 for word in query_words if word in snippet_part)
            if word_matches > max_word_matches:
                max_word_matches = word_matches
                best_match_pos = i
        
        if best_match_pos >= 0:
            start = max(0, best_match_pos - 100)
            end = min(len(body), best_match_pos + 300)
            best_snippet = body[start:end].strip()
            if start > 0:
                best_snippet = "..." + best_snippet
            if end < len(body):
                best_snippet = best_snippet + "..."
        else:
            best_snippet = body[:max_context] + "..." if len(body) > max_context else body
            
        results.append({
            'title': page['title'],
            'url': page['url'],
            'snippet': best_snippet,
            'relevance_score': score,
            'precision_level': 'high' if score >= 8 else 'medium' if score >= 4 else 'low'
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
    """Legacy wrapper for backward compatibility - now uses enhanced analysis"""
    ai_context = AIContext(conv_id, user_message)
    ai_context.ai_response = ai_response
    analysis = analyze_conversation_for_problems(ai_context)
    
    if analysis:
        # Convert new format to legacy format for compatibility
        problems = analysis.get("problems", [])
        overall = analysis.get("overall_analysis", {})
        
        return {
            "needs_support": overall.get("needs_support", False),
            "problem_description": problems[0]["description"] if problems else user_message[:50] + "...",
            "core_problem": problems[0]["title"] if problems else user_message[:50] + "...",
            "matching_services": [],  # This is now handled by smart service detection
            "confidence": overall.get("confidence", 0),
            "is_critical": overall.get("is_critical", False)
        }
    
    return {
        "needs_support": False,
        "problem_description": user_message[:50] + "..." if len(user_message) > 50 else user_message,
        "core_problem": user_message[:50] + "..." if len(user_message) > 50 else user_message,
        "matching_services": [],
        "confidence": 0,
        "is_critical": False
    }

def analyze_conversation_for_problems(ai_context: AIContext):
    """Enhanced AI analysis to identify multiple specific problems"""
    if not client:
        return None
    
    try:
        context_info = ai_context.get_full_context()
        
        # Enhanced problem analysis prompt
        analysis_prompt = f"""
Хэрэглэгчийн бүх харилцлага: {context_info['full_conversation']}
AI хариулт: {ai_context.ai_response}

Энэ харилцлагыг дүн шинжилж дараах ажлуудыг гүйцэтгэ:

1. АСУУДЛУУДЫГ ялгаж тодорхойл (хэрэв олон асуудал байвал тус тусад нь)
2. Техникийн нарийвчлалын түвшинг үнэлэ  
3. Дэмжлэгийн шаардлагыг тодорхойл
4. Microsoft Teams руу илгээх шаардлагатай эсэхийг шийд

JSON хэлбэрээр хариулна уу:
{{
    "problems": [
        {{
            "title": "асуудлын товч нэр",
            "description": "дэлгэрэнгүй тайлбар", 
            "category": "technical/general/service_request",
            "priority": "high/medium/low",
            "complexity": "simple/moderate/complex"
        }}
    ],
    "overall_analysis": {{
        "needs_support": true/false,
        "is_critical": true/false,
        "confidence": 0-100,
        "user_satisfaction": "high/medium/low",
        "requires_teams_notification": true/false,
        "found_in_docs": {getattr(ai_context, 'found_in_docs', False)}
    }},
    "recommended_action": "continue_chat/escalate_to_support/provide_service_info/mark_resolved"
}}
        """
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "Та мэргэжлийн техникийн дэмжлэгийн шинжээч. Асуудлуудыг тодорхойлж, шийдвэрлэх арга замыг санал болгодог."
                },
                {
                    "role": "user",
                    "content": analysis_prompt
                }
            ],
            max_tokens=500,
            temperature=0.3
        )
        
        analysis_text = response.choices[0].message.content.strip()
        json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
        
        if json_match:
            analysis_result = json.loads(json_match.group())
            ai_context.analysis_result = analysis_result
            return analysis_result
        
        return None
        
    except Exception as e:
        logging.error(f"Enhanced problem analysis failed: {e}")
        return None

def suggest_services_from_analysis(matching_services: list):
    """Generate intelligent service suggestions based on analysis"""
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

def smart_service_detection(ai_context: AIContext):
    """Smart service detection using AI context"""
    if not client:
        return []
    
    try:
        context_info = ai_context.get_full_context()
        service_list = "\n".join([f"- {key}" for key in SERVICE_PRICES.keys()])
        
        detection_prompt = f"""
Хэрэглэгчийн харилцлага: {context_info['full_conversation']}
AI хариулт: {ai_context.ai_response or ''}

Боломжтой үйлчилгээнүүд:
{service_list}

Хэрэглэгчийн асуудалтай хамгийн тохирох үйлчилгээнүүдийг жагсаана уу:

JSON хэлбэрээр хариулна уу:
{{
    "matching_services": ["үйлчилгээний жагсаалт"],
    "confidence": 0-100
}}
        """
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "Та үйлчилгээний тохирлыг тодорхойлох мэргэжилтэн."
                },
                {
                    "role": "user",
                    "content": detection_prompt
                }
            ],
            max_tokens=150,
            temperature=0.2
        )
        
        result = response.choices[0].message.content.strip()
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        
        if json_match:
            detection_result = json.loads(json_match.group())
            return detection_result.get("matching_services", [])
        
        return []
        
    except Exception as e:
        logging.error(f"Smart service detection failed: {e}")
        return []

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

def send_teams_notification(conv_id: int, message: str, message_type: str = "outgoing", is_unsolved: bool = False, confirmed: bool = False, user_email: str = None, original_question: str = ""):
    """Send notification to Teams about core problems only (confirmed critical issues)"""
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
        
        # Only send to Teams if confirmed and there's a specific problem
        if confirmed and user_email:
            # Get email from conversation or use contact email as fallback
            display_email = user_email if user_email else contact_email
            
            # Analyze the entire conversation to identify the core issue
            conversation_history = conversation_memory.get(conv_id, [])
            user_messages = []
            for msg in conversation_history:
                if msg.get("role") == "user":
                    user_messages.append(msg.get("content", ""))
            
            # Get core problem using AI analysis
            core_problem = ""
            if original_question and client:
                try:
                    # Use the full conversation context to get precise problem description
                    full_context = "\n".join(user_messages) if user_messages else original_question
                    
                    problem_analysis = client.chat.completions.create(
                        model="gpt-4",
                        messages=[
                            {
                                "role": "system",
                                "content": "Та хэрэглэгчийн асуудлыг цэгцтэй тодорхойлж өгөх мэргэжилтэн. Дэмжлэгийн багт ойлгомжтой, үйлдэлд чиглэсэн тайлбар өг."
                            },
                            {
                                "role": "user",
                                "content": f"Хэрэглэгчийн бүх харилцлага: '{full_context}'\n\nЭнэ хэрэглэгчийн ХАМГИЙН ЧУХАЛ асуудлыг 1 өгүүлбэрээр тодорхой тайлбарлаж, дэмжлэгийн багт хэрхэн шийдвэрлэх талаар мэдээлэл өг:"
                            }
                        ],
                        max_tokens=100,
                        temperature=0.2
                    )
                    core_problem = problem_analysis.choices[0].message.content.strip()
                except Exception as e:
                    logging.error(f"Core problem analysis failed: {e}")
                    core_problem = original_question[:100] + "..." if len(original_question) > 100 else original_question
            else:
                core_problem = original_question[:100] + "..." if len(original_question) > 100 else original_question
            
            # Create focused Teams message with only essential information
            teams_message = f"""
🚨 ЧУХАЛ АСУУДАЛ - Cloud.mn

👤 Хэрэглэгч: {contact_name}
📧 Имэйл: {display_email}
🆔 Conversation ID: {conv_id}

⚠️ **Асуудал:**
{core_problem}

🕒 Огноо: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
🔗 Chatwoot: {CHATWOOT_BASE_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}
            """
            
            # Send to Teams with critical color (red/orange)
            send_to_teams(
                message=teams_message,
                title=f"🚨 ЧУХАЛ - {contact_name}",
                color="FF6B35",  # Orange/red color for critical issues
                conv_id=conv_id
            )
            
            logging.info(f"Critical issue sent to Teams for conversation {conv_id}")
        
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
    """Enhanced conversational webhook - ChatGPT style interaction"""
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

    # Auto-crawl status check
    if crawl_status["status"] == "not_started" and AUTO_CRAWL_ON_START:
        auto_crawl_on_startup()

    # Handle special commands (minimal, only essential ones)
    if text.lower() in ["тусламж", "help", "туслах"]:
        help_text = f"""
👋 Сайн байна уу {contact_name}! Би Cloud.mn-ийн AI туслах.

📊 **Төлөв:**
{'✅ Баримт бичиг бэлэн байна' if crawl_status["status"] == "completed" else '🔄 Системийг бэлтгэж байна...'}

💬 **Хэрхэн ашиглах:**
• Асуултаа эсвэл асуудлаа энгийн хэлээр бичнэ үү
• Би эхлээд docs.cloud.mn-аас хайж, хариулна
• Олдохгүй бол миний мэдлэгээр туслана
• Шаардлагатай бол мэргэжлийн дэмжлэгт холбож өгнө

🔧 **Жишээ асуултууд:**
• "Docker хэрхэн суулгах вэ?"
• "Nginx тохируулах заавар хэрэгтэй"
• "Серверийн алдаа гарч байна"

Асуултаа чөлөөтэй бичээрэй! 😊
        """
        send_to_chatwoot(conv_id, help_text)

    elif text.lower() in ["баяртай", "goodbye", "баай", "дууслаа"]:
        response = f"👋 Баяртай {contact_name}! Дараа дахин тусламж хэрэгтэй бол эргээд ирээрэй!"
        send_to_chatwoot(conv_id, response)
        mark_conversation_resolved(conv_id)

    # Handle email confirmation workflows
    elif text.lower() in ["цуцлах", "cancel", "үгүй цуцлах"]:
        memory = conversation_memory.get(conv_id, [])
        if memory and ("pending_confirmation" in memory[-1].get("content", "") or "waiting_for_email" in memory[-1].get("content", "")):
            send_to_chatwoot(conv_id, "✅ Ойлголоо. Харилцлагыг үргэлжлүүлцгээе.")
            # Clear waiting states
            conversation_memory[conv_id] = [msg for msg in conversation_memory[conv_id] 
                                         if not any(state in msg.get("content", "") for state in ["pending_confirmation", "waiting_for_email"])]
        else:
            # Handle as normal conversation
            process_conversational_message(conv_id, text, contact_name)

    else:
        # Check if this is a response to confirmation or email request
        memory = conversation_memory.get(conv_id, [])
        
        if memory and "pending_confirmation" in memory[-1].get("content", ""):
            handle_confirmation_response(conv_id, text, contact_name)
        elif memory and "waiting_for_email" in memory[-1].get("content", ""):
            handle_email_response(conv_id, text, contact_name)
        else:
            # Normal conversational interaction
            process_conversational_message(conv_id, text, contact_name)

    return jsonify({"status": "success"}), 200

def process_conversational_message(conv_id: int, text: str, contact_name: str):
    """Process normal conversational messages with enhanced AI"""
    
    # Create AI context
    ai_context = AIContext(conv_id, text)
    
    # Get enhanced AI response
    ai_response = get_enhanced_ai_response(ai_context)
    send_to_chatwoot(conv_id, ai_response)
    
    # Enhanced analysis and decision making
    analysis = analyze_conversation_for_problems(ai_context)
    
    if analysis:
        overall = analysis.get("overall_analysis", {})
        problems = analysis.get("problems", [])
        recommended_action = analysis.get("recommended_action", "continue_chat")
        
        # Log analysis for debugging
        logging.info(f"Analysis for conv {conv_id}: {len(problems)} problems, action: {recommended_action}, critical: {overall.get('is_critical', False)}")
        
        # Handle different scenarios based on analysis
        if overall.get("requires_teams_notification", False) or (overall.get("is_critical", False) and overall.get("needs_support", False)):
            # Send detailed Teams notification immediately for critical issues
            teams_sent = send_enhanced_teams_notification(ai_context, analysis)
            
            if teams_sent:
                confirmation_message = f"""
🚨 Таны асуудал мэргэжлийн дэмжлэгийн багт автоматаар илгээгдлээ.

📋 **Илрүүлсэн асуудлууд:** {len(problems)}
📊 **Чухал байдал:** {'🔴 Өндөр' if overall.get('is_critical') else '🟡 Дунд зэрэг'}

Дэмжлэгийн баг тун удахгүй танай асуудлыг шийдвэрлэхээр холбогдох болно.

Өөр асуулт байвал чөлөөтэй асуугаарай! 😊
                """
                send_to_chatwoot(conv_id, confirmation_message)
                
        elif recommended_action == "escalate_to_support" and overall.get("needs_support", False):
            # Ask for confirmation before escalating
            core_problem = problems[0]["title"] if problems else "Техникийн асуудал"
            confirmation_message = f"""
🤔 Таны асуудал: "{core_problem}"

Энэ асуудлыг мэргэжлийн дэмжлэгийн багт шилжүүлэх үү?

✅ **Тийм** - дэмжлэгийн багт илгээх
❌ **Үгүй** - харилцлагыг үргэлжлүүлэх

Та сонголтоо бичнэ үү.
            """
            send_to_chatwoot(conv_id, confirmation_message)
            
            # Store confirmation state
            if conv_id not in conversation_memory:
                conversation_memory[conv_id] = []
            conversation_memory[conv_id].append({"role": "assistant", "content": confirmation_message + " pending_confirmation"})
                
        elif recommended_action == "provide_service_info":
            # Smart service suggestions
            services = smart_service_detection(ai_context)
            if services:
                service_suggestions = suggest_services_from_analysis(services)
                if service_suggestions:
                    send_to_chatwoot(conv_id, service_suggestions)
                    
        elif recommended_action == "mark_resolved" and overall.get("user_satisfaction") == "high":
            # Offer to close if user seems satisfied
            close_suggestion = f"""
✅ Таны асуулт хангалттай хариулагдсан бололтой!

Өөр асуулт байвал чөлөөтэй асуугаарай, эсвэл харилцлагыг дуусгахыг хүсвэл "баяртай" гэж бичнэ үү. 😊
            """
            send_to_chatwoot(conv_id, close_suggestion)

def handle_confirmation_response(conv_id: int, text: str, contact_name: str):
    """Handle user response to Teams escalation confirmation"""
    
    if not client:
        send_to_chatwoot(conv_id, "🔑 AI систем одоогоор боломжгүй байна. Дахин оролдоно уу.")
        return
    
    try:
        # Use AI to understand the response
        confirmation_response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "Хэрэглэгчийн хариултыг дүгнэж зөвшөөрөл эсвэл татгалзлыг тодорхойл. 'yes' эсвэл 'no' гэж хариулна уу."
                },
                {
                    "role": "user",
                    "content": f"Хэрэглэгчийн хариулт: {text}\n\nЭнэ зөвшөөрөл мөн үү?"
                }
            ],
            max_tokens=10,
            temperature=0.2
        )
        
        is_confirmed = confirmation_response.choices[0].message.content.strip().lower() == "yes"
        
        if is_confirmed:
            # Request email
            email_request = f"""
✅ Баярлалаа {contact_name}!

Дэмжлэгийн багт танай асуудлыг илгээхийн тулд email хаягаа бичнэ үү?

📧 **Жишээ:** example@gmail.com

💡 Хэрэв email өгөхгүй бол "цуцлах" гэж бичнэ үү.
            """
            send_to_chatwoot(conv_id, email_request)
            
            # Update conversation state
            conversation_memory[conv_id].append({"role": "assistant", "content": "waiting_for_email"})
        else:
            send_to_chatwoot(conv_id, f"✅ Ойлголоо {contact_name}. Өөр асуулт байвал чөлөөтэй асуугаарай!")
            # Clear confirmation state
            conversation_memory[conv_id] = [msg for msg in conversation_memory[conv_id] 
                                         if "pending_confirmation" not in msg.get("content", "")]
            
    except Exception as e:
        logging.error(f"Error handling confirmation: {e}")
        send_to_chatwoot(conv_id, "🔧 Системийн алдаа гарлаа. Дахин оролдоно уу.")

def handle_email_response(conv_id: int, text: str, contact_name: str):
    """Handle user email input"""
    
    import re
    
    # Validate email format
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    if re.match(email_pattern, text.strip()):
        user_email = text.strip()
        
        # Get the conversation context for Teams
        memory = conversation_memory.get(conv_id, [])
        
        # Find the original problem context
        original_question = ""
        ai_response = ""
        
        # Look for the AI response before email collection started
        for i, msg in enumerate(memory):
            if "waiting_for_email" in msg.get("content", ""):
                # Find previous user and AI messages
                if i >= 2:
                    ai_response = memory[i-2].get("content", "")
                    if i >= 3:
                        original_question = memory[i-3].get("content", "")
                break
        
        # Create enhanced AI context for Teams notification
        ai_context = AIContext(conv_id, original_question or "Email холбогдох хүсэлт")
        ai_context.ai_response = ai_response
        
        # Analyze the conversation for detailed Teams notification
        analysis = analyze_conversation_for_problems(ai_context)
        
        if analysis:
            # Send to Teams with detailed analysis
            teams_sent = send_enhanced_teams_notification(ai_context, analysis)
            
            if teams_sent:
                send_to_chatwoot(conv_id, f"""
✅ Амжилттай! Таны асуудлыг дэмжлэгийн багт илгээлээ.

📧 **Email:** {user_email}
⏰ **Хүлээх хугацаа:** 1-2 ажлын өдөр

Дэмжлэгийн баг танай email хаяг руу холбогдох болно.

Өөр асуулт байвал чөлөөтэй асуугаарай! 😊
                """)
            else:
                send_to_chatwoot(conv_id, "❌ Teams руу илгээхэд алдаа гарлаа. Дахин оролдоно уу.")
        else:
            # Fallback - send basic Teams notification
            context_info = ai_context.get_full_context()
            basic_teams_message = f"""
🚨 **ДЭМЖЛЭГ ХҮСЭЛТ**

👤 **Хэрэглэгч:** {contact_name}
📧 **Имэйл:** {user_email}
🆔 **Conversation ID:** {conv_id}

💬 **Харилцлага:**
{context_info['full_conversation'][-500:]}

🕒 **Огноо:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            """
            
            teams_sent = send_to_teams(
                message=basic_teams_message,
                title=f"📧 {contact_name} - Дэмжлэг хүсэлт",
                color="FF6B35",
                conv_id=conv_id
            )
            
            if teams_sent:
                send_to_chatwoot(conv_id, f"✅ Таны асуудлыг дэмжлэгийн багт илгээлээ ({user_email}). Тун удахгүй холбогдох болно.")
        
        # Clear waiting state
        conversation_memory[conv_id] = [msg for msg in conversation_memory[conv_id] 
                                     if "waiting_for_email" not in msg.get("content", "")]
        
    else:
        send_to_chatwoot(conv_id, """
❌ Буруу email хэлбэр байна.

📧 **Зөв хэлбэр:** example@gmail.com
💡 **Цуцлах:** "цуцлах" гэж бичнэ үү
        """)

# Legacy function for backward compatibility  
def get_ai_response(user_message: str, conversation_id: int, context_data: list = None):
    """Legacy wrapper for backward compatibility"""
    ai_context = AIContext(conversation_id, user_message)
    return get_enhanced_ai_response(ai_context)


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

def contains_service_or_support(text):
    """Enhanced service and support detection"""
    text_lower = text.lower()
    found_service = any(service.lower() in text_lower for service in SERVICE_KEYWORDS)
    return found_service

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
