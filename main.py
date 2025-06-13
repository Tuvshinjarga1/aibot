from fastapi import FastAPI, Request
import uvicorn
import requests

app = FastAPI()

@app.post("/webhook/chatwoot")

async def webhook(request: Request, token):

    body = await request.json()

    message = body.get("content")

    if message == "Hi":

        url = https://app.chatwoot.com/api/v1/accounts/123470/conversations/5/messages

        data = {"content": "Hello, bi tanid yj tuslah ve?"}

        headers = {"api_access_token": "Go61PtbAmeXrmmQineSiQyv3"}

        response = requests.post(url, json=data, headers=headers)

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0", port=8000, reload=True)