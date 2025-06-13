from fastapi import FastAPI, Request
import uvicorn
import requests
import os

app = FastAPI()

@app.post("/webhook/chatwoot")
async def webhook(request: Request):
    try:
        body = await request.json()
        message = body.get("content")

        if message == 'Hi':
            url = "https://app.chatwoot.com/api/v1/accounts/123470/conversations/5/messages"

            data = {
                "content": "Hello, би танд юугаар туслах вэ?",
                "message_type": "outgoing"
            }

            headers = {
                "api_access_token": os.getenv("CHATWOOT_API_KEY"),
                "Content-Type": "application/json"
            }

            response = requests.post(url, json=data, headers=headers)

            if response.status_code == 200:
                print(f"Success: {response.status_code}, Response: {response.text}")
            else:
                print(f"Error: {response.status_code}, Response: {response.text}")

        return {"status": "received"}

    except Exception as e:
        print(f"An error occurred: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)