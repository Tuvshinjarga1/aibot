from fastapi import FastAPI, Request
import requests

app = FastAPI()

CHATWOOT_BASE_URL = "https://app.chatwoot.com"
ACCOUNT_ID = "123470"
CONVERSATION_ID = "5"
CHATWOOT_API_KEY = "Go61PtbAmeXrmmQineSiQyv3"  # Жинхэнэ токеноо .env ашиглаж солих хэрэгтэй

@app.post("/webhook/chatwoot")
async def webhook(request: Request):
    body = await request.json()
    message = body.get("content") or body.get("message", {}).get("content")

    if message == "Hi":
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{CONVERSATION_ID}/messages"
        data = {
            "content": "Hello, би танд юугаар туслах вэ?",
            "message_type": "outgoing"
        }
        headers = {
            "api_access_token": CHATWOOT_API_KEY,
            "Content-Type": "application/json"
        }
        response = requests.post(url, json=data, headers=headers)
        print(f"Status: {response.status_code}, Response: {response.text}")

    return {"status": "received"}
