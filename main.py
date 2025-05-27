# main.py
from fastapi import FastAPI, Request
import asyncio
from pydantic import BaseModel
import httpx
import os
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()

OPENAI_API_KEY = os.getenv("OPENROUTER_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_API_KEY")

user_threads = {}

class ChatwootRequest(BaseModel):
    content: str

@app.post("/api/chatwoot")
async def chatwoot_webhook(data: ChatwootRequest, request: Request):
    body = await request.json()
    print("📥 Body from Chatwoot:", body)

    try:
        sender = body.get("sender") or body.get("meta", {}).get("sender")
        user_id = str(sender.get("id") if sender else "anonymous")

        if user_id not in user_threads:
            thread_id = await create_thread()
            user_threads[user_id] = thread_id
            print(f"✅ New thread_id: {thread_id}")
        else:
            thread_id = user_threads[user_id]
            print(f"🧵 Using thread_id={thread_id} for user={user_id}")

        content = body.get("content", data.content or "...")
        print("✉️ Sending message to assistant:", content)

        reply = await get_assistant_response(content, thread_id)
        return {"content": reply}

    except Exception as e:
        print("⚠️ Error while handling webhook:", e)
        return {"content": "⚠️ Алдаа гарлаа."}


async def create_thread() -> str:
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://api.openai.com/v1/threads",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            }
        )
        return res.json()["id"]


async def wait_for_active_run(thread_id: str):
    async with httpx.AsyncClient() as client:
        while True:
            res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2"
                }
            )
            runs = res.json().get("data", [])
            if not runs:
                return

            latest_run = runs[0]
            if latest_run["status"] in ["queued", "in_progress"]:
                print(f"⏳ Waiting for existing run {latest_run['id']} to finish...")
                await asyncio.sleep(1)
            else:
                return


async def get_assistant_response(message: str, thread_id: str) -> str:
    async with httpx.AsyncClient() as client:
        # Wait if active run exists
        await wait_for_active_run(thread_id)

        # Add message
        await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"role": "user", "content": message}
        )

        # Start run
        run_res = await client.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2",
                "Content-Type": "application/json"
            },
            json={"assistant_id": ASSISTANT_ID}
        )

        run_data = run_res.json()
        run_id = run_data.get("id")
        if not run_id:
            print("❌ Run creation failed:", run_data)
            return "⚠️ Хариу боловсруулах боломжгүй байна."

        # Wait for run to complete
        while True:
            status_res = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2"
                }
            )
            status = status_res.json().get("status", "")
            if status == "completed":
                break
            elif status == "failed":
                return "⚠️ Ассистант ажиллахад алдаа гарлаа."
            await asyncio.sleep(1)

        # Get final message
        messages_res = await client.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "assistants=v2"
            }
        )
        messages = messages_res.json().get("data", [])
        return messages[0]["content"][0]["text"]["value"] if messages else "⚠️ Хариу ирсэнгүй."