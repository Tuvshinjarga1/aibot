import requests

url = "https://app.chatwoot.com/api/v1/accounts/125294/conversations/13"
headers = {"api_access_token": "piwnJ8h9h6yLMcTYAh4FTRyu"}

response = requests.get(url, headers=headers)
data = response.json()

# Meta хэсгээс авах (хамгийн найдвартай)
assignee_id = data["meta"]["assignee"]["id"]
print("Assignee ID:", assignee_id)
