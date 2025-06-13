from fastapi import FastAPI, Request
import uvicorn
import requests
import os
from dotenv import load_dotenv

load_dotenv()  # .env файлыг уншуулна

app = FastAPI()

@app.post("/webhook/chatwoot")
async def webhook(request: Request):
    try:
        body = await request.json()
        message = body.get("message", {}).get("content")

        if message == 'Hi':
            url = "https://app.chatwoot.com/api/v1/accounts/123470/conversations/12/messages"

            data = {
                "content": "Hello, би танд юугаар туслах вэ?",
                "message_type": "outgoing"
            }

            headers = {
                "api_access_token": os.getenv("CHATWOOT_API_KEY"),
                "Content-Type": "application/json"
            }

            print("📤 Sending message to Chatwoot...")
            response = requests.post(url, json=data, headers=headers)

            print(f"📥 Chatwoot response: {response.status_code} - {response.text}")

        return {"status": "received"}

    except Exception as e:
        print(f"❌ Error: {e}")
        return {"status": "error", "message": str(e)}

# Gunicorn-д зориулж ASGI worker-ийг тохируулах
app = app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)