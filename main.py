from fastapi import FastAPI, Request
import gunicorn
import requests

app = FastAPI()

@app.post("/webhook/chatwoot")

async def webhook(request: Request):

    body = await request.json()

    message = body.get("content")

    if message == 'Hi':

        url = "https://app.chatwoot.com/api/v1/accounts/123470/conversations/5/messages"

        data = {
            "content": "Hello, би танд юугаар туслах вэ?",
            "message_type": "outgoing"
        }

        headers = {
            "api_access_token": "Go61PtbAmeXrmmQineSiQyv3",
            "Content-Type": "application/json"
        }

        response = requests.post(url, json=data, headers=headers)
        print(f"Status: {response.status_code}, Response: {response.text}")

    return {"status": "received"}

if __name__ == "__main__":
    gunicorn.run(app, host="0.0.0.0", port=8000, reload=True)