import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

CHATWOOT_API_KEY = os.getenv("CHATWOOT_API_KEY", "").strip()
ACCOUNT_ID = os.getenv("ACCOUNT_ID", "").strip()
CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL", "https://app.chatwoot.com").rstrip("/")

def test_chatwoot_api():
    """Chatwoot API тохиргоог шалгах"""
    print("🔍 Chatwoot API тохиргоог шалгаж байна...")
    print(f"📍 Base URL: {CHATWOOT_BASE_URL}")
    print(f"🔑 API Key: {CHATWOOT_API_KEY[:10]}..." if CHATWOOT_API_KEY else "❌ API Key байхгүй")
    print(f"🏢 Account ID: {ACCOUNT_ID}")
    
    if not CHATWOOT_API_KEY:
        print("❌ CHATWOOT_API_KEY тохируулаагүй байна!")
        return False
    
    if not ACCOUNT_ID:
        print("❌ ACCOUNT_ID тохируулаагүй байна!")
        return False
    
    try:
        # Test 1: Account мэдээлэл авах
        print("\n📋 Test 1: Account мэдээлэл авч байна...")
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}"
        headers = {"api_access_token": CHATWOOT_API_KEY}
        
        response = requests.get(url, headers=headers)
        print(f"📊 Response status: {response.status_code}")
        
        if response.status_code == 200:
            account_data = response.json()
            print(f"✅ Account нэр: {account_data.get('name', 'Тодорхойгүй')}")
            print(f"✅ Account ID: {account_data.get('id', 'Тодорхойгүй')}")
        elif response.status_code == 401:
            print("❌ 401 Unauthorized - API key буруу байна!")
            print("💡 Chatwoot дээр Settings > Applications > API Access Tokens шалгана уу")
            return False
        elif response.status_code == 404:
            print("❌ 404 Not Found - Account ID буруу байна!")
            print("💡 Chatwoot URL-аас зөв account ID авна уу")
            return False
        else:
            print(f"❌ Алдаа: {response.status_code} - {response.text}")
            return False
        
        # Test 2: Conversations жагсаалт авах
        print("\n📋 Test 2: Conversations жагсаалт авч байна...")
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
        response = requests.get(url, headers=headers)
        print(f"📊 Response status: {response.status_code}")
        
        if response.status_code == 200:
            conversations = response.json()
            conv_count = len(conversations.get('data', []))
            print(f"✅ {conv_count} conversation олдлоо")
        else:
            print(f"⚠️ Conversations авахад алдаа: {response.status_code}")
        
        # Test 3: Inboxes жагсаалт авах
        print("\n📋 Test 3: Inboxes жагсаалт авч байна...")
        url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/inboxes"
        response = requests.get(url, headers=headers)
        print(f"📊 Response status: {response.status_code}")
        
        if response.status_code == 200:
            inboxes = response.json()
            print(f"✅ {len(inboxes)} inbox олдлоо:")
            for inbox in inboxes:
                print(f"   📥 {inbox.get('name')} (ID: {inbox.get('id')}) - {inbox.get('channel_type')}")
        else:
            print(f"⚠️ Inboxes авахад алдаа: {response.status_code}")
        
        print("\n✅ Chatwoot API тохиргоо зөв байна!")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Network алдаа: {e}")
        print("💡 Интернет холболт болон Chatwoot URL шалгана уу")
        return False
    except Exception as e:
        print(f"❌ Алдаа: {e}")
        return False

if __name__ == "__main__":
    test_chatwoot_api() 