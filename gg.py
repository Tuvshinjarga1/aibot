import requests

url = "https://app.chatwoot.com/api/v1/accounts/125294/conversations/13"

headers = {"api_access_token": "piwnJ8h9h6yLMcTYAh4FTRyu"}

response = requests.request("GET", url, headers=headers)

print(response.text)