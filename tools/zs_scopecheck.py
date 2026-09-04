import os
# Standalone OneAPI scope check. Reads secret from ~/.zscaler_api_key.
# Does NOT import from or write to the ebc-dashboard project.
# Prints token CLAIMS only -- never the secret, never the bearer token.
import base64, json, os, urllib.parse, urllib.request

ISSUER = os.environ.get("ZS_ISSUER", "https://YOUR_TENANT.zslogin.net")
CLIENT_ID = os.environ.get("ZS_CLIENT_ID", "")
sec = open(os.path.expanduser("~/.zscaler_api_key")).read().strip()

data = urllib.parse.urlencode({
    "grant_type": "client_credentials",
    "client_id": CLIENT_ID,
    "client_secret": sec,
    "audience": "https://api.zscaler.com",
}).encode()
req = urllib.request.Request(f"{ISSUER}/oauth2/v1/token", data=data,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        tok = json.load(r)["access_token"]
except Exception as e:
    print("TOKEN REQUEST FAILED:", type(e).__name__, e)
    body = getattr(e, "read", lambda: b"")()
    if body: print("body:", body[:400].decode("utf8", "replace"))
    raise SystemExit(1)

p = tok.split(".")[1]
c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
print("AUTH OK -- token acquired (value withheld)\n")
for k in ("iss", "aud", "sub", "client_id", "exp", "scope", "scp", "permissions"):
    if k in c: print(f"{k:12} = {c[k]}")
print("\nservice-info / entitlements:")
print(json.dumps(c.get("service-info") or c.get("services") or {}, indent=2)[:2500])
print("\nall claim keys:", sorted(c.keys()))
