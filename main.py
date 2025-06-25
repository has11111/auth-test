import os, secrets, base64, hashlib
import requests
from fastapi import FastAPI, HTTPException, Response
from cryptography.fernet import Fernet
from typing import Any, Dict

app = FastAPI()
oauth_state_cache = {}
user_tokens = {}
fernet = Fernet(os.environ["ENCRYPTION_KEY"].encode())
API_BASE = "https://quickbooks.api.intuit.com/v3/company"

@app.get("/oauth/login")
def oauth_login(response: Response):
    state = secrets.token_urlsafe(16)
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()
    oauth_state_cache[state] = code_verifier
    params = {
        "client_id": os.environ["QBO_CLIENT_ID"],
        "response_type": "code",
        "scope": "com.intuit.quickbooks.accounting",
        "redirect_uri": os.environ["QBO_REDIRECT_URI"],
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }
    auth_url = "https://appcenter.intuit.com/connect/oauth2?" + "&".join(f"{k}={requests.utils.requote_uri(v)}" for k,v in params.items())
    response.status_code = 302
    response.headers["Location"] = auth_url
    return {"detail": "Redirecting to QuickBooks for authorization..."}

@app.get("/oauth/callback")
def oauth_callback(code: str, state: str, realmId: str):
    verifier = oauth_state_cache.get(state)
    if not verifier:
        raise HTTPException(status_code=400, detail="Invalid state")
    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    auth = (os.environ["QBO_CLIENT_ID"], os.environ["QBO_CLIENT_SECRET"])
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": os.environ["QBO_REDIRECT_URI"],
        "code_verifier": verifier
    }
    res = requests.post(token_url, data=data, auth=auth)
    if res.status_code != 200:
        raise HTTPException(status_code=500, detail="Token exchange failed")
    tokens = res.json()
    user_tokens[state] = {
        "realm_id": realmId,
        "access_token": tokens["access_token"],
        "refresh_token_enc": fernet.encrypt(tokens["refresh_token"].encode()).decode()
    }
    return {"detail": "QuickBooks authorization successful. Return to ChatGPT."}

def qbo_get(user_id: str, path: str, params=None):
    tokens = user_tokens.get(user_id)
    if not tokens:
        raise HTTPException(status_code=401, detail="Not authorized. Please log in.")
    headers = {
        "Authorization": f"Bearer {tokens['access_token']}",
        "Accept": "application/json"
    }
    url = f"{API_BASE}/{tokens['realm_id']}/{path}"
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 401:
        # Try refresh
        refresh_token = fernet.decrypt(tokens["refresh_token_enc"].encode()).decode()
        auth = (os.environ["QBO_CLIENT_ID"], os.environ["QBO_CLIENT_SECRET"])
        r = requests.post("https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
                          data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                          auth=auth)
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Token refresh failed")
        new_tokens = r.json()
        tokens["access_token"] = new_tokens["access_token"]
        if "refresh_token" in new_tokens:
            tokens["refresh_token_enc"] = fernet.encrypt(new_tokens["refresh_token"].encode()).decode()
        headers["Authorization"] = f"Bearer {tokens['access_token']}"
        res = requests.get(url, headers=headers, params=params)
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=f"QBO API error: {res.text}")
    return res.json()

def search_qbo(user_id, entity, query):
    if entity == "customer":
        q = f"SELECT Id, DisplayName FROM Customer WHERE DisplayName LIKE '%{query}%'"
    elif entity == "invoice":
        q = f"SELECT Id, DocNumber, TotalAmt, Balance FROM Invoice WHERE DocNumber LIKE '%{query}%'"
    elif entity == "payment":
        q = f"SELECT Id, TotalAmt, CustomerRef FROM Payment WHERE PrivateNote LIKE '%{query}%'"
    elif entity == "item":
        q = f"SELECT Id, Name, Type FROM Item WHERE Name LIKE '%{query}%'"
    else:
        raise HTTPException(status_code=400, detail="Invalid entity")
    result = qbo_get(user_id, "query", params={"query": q})
    return result.get("QueryResponse", {}).get(entity.capitalize(), [])

def fetch_qbo(user_id, entity, record_id):
    result = qbo_get(user_id, f"{entity}/{record_id}")
    return result.get(entity.capitalize())

@app.get("/mcp/discover")
def discover():
    return {
        "tools": [
            {
                "name": "search",
                "description": "Search QBO for Customers, Invoices, Payments, or Items.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string"},
                        "query": {"type": "string"}
                    },
                    "required": ["entity", "query"]
                }
            },
            {
                "name": "fetch",
                "description": "Fetch a QBO record by ID.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string"},
                        "id": {"type": "string"}
                    },
                    "required": ["entity", "id"]
                }
            }
        ]
    }

@app.post("/mcp/execute")
def execute(request: Dict[str, Any]):
    endpoint = request.get("endpoint")
    params = request.get("parameters", {})
    user_id = request.get("user_id") or request.get("state")
    if endpoint == "search":
        results = search_qbo(user_id, params["entity"], params["query"])
        text = f"Found {len(results)} result(s).\n"
        for r in results[:5]:
            text += str(r) + "\n"
        return {"content": [{"type": "text", "text": text}]}
    elif endpoint == "fetch":
        r = fetch_qbo(user_id, params["entity"], params["id"])
        return {"content": [{"type": "text", "text": str(r)}]}
    else:
        raise HTTPException(status_code=400, detail="Unknown tool")