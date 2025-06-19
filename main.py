from fastapi import FastAPI, Request
import httpx
from urllib.parse import quote

app = FastAPI()

# Replace these with your Intuit Dev App credentials
CLIENT_ID = "YOUR_DEV_CLIENT_ID"
CLIENT_SECRET = "YOUR_DEV_CLIENT_SECRET"
REDIRECT_URI = "https://qbo-oauth-dev-test.onrender.com/callback"  # will update later after Render deploy
STATE = "test123"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

@app.get("/")
def home():
    return {"message": "QBO OAuth Test App. Go to /auth to start."}

@app.get("/auth")
def auth():
    scope = "com.intuit.quickbooks.accounting"
    auth_url = (
        f"https://sandbox.appcenter.intuit.com/connect/oauth2"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={quote(REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope={quote(scope)}"
        f"&state={STATE}"
    )
    return {"auth_url": auth_url}

@app.get("/callback")
async def callback(request: Request):
    code = request.query_params.get("code")
    realm_id = request.query_params.get("realmId")

    if not code:
        return {"error": "Authorization failed"}

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    auth = httpx.BasicAuth(CLIENT_ID, CLIENT_SECRET)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(TOKEN_URL, headers=headers, data=data, auth=auth)

    if res.status_code != 200:
        return {"error": "Token exchange failed", "details": res.json()}

    tokens = res.json()
    with open("tokens.txt", "w") as f:
        f.write(f"ACCESS_TOKEN={tokens['access_token']}\n")
        f.write(f"REFRESH_TOKEN={tokens['refresh_token']}\n")
        f.write(f"REALM_ID={realm_id}\n")

    return {"message": "✅ Tokens saved"}