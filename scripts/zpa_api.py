import os
"""Minimal ZPA OneAPI client. Standalone: no dependencies outside the stdlib and
no coupling to anything else in the tenant."""
import base64,json,os,urllib.parse,urllib.request,urllib.error
ISSUER=os.environ.get("ZS_ISSUER","https://YOUR_TENANT.zslogin.net"); GW="https://api.zsapi.net"; CID=os.environ.get("ZS_CLIENT_ID","")
_tok=None; _cust=None
def _auth():
    global _tok,_cust
    if _tok: return _tok,_cust
    sec=open(os.path.expanduser("~/.zscaler_api_key")).read().strip()
    d=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":CID,
      "client_secret":sec,"audience":"https://api.zscaler.com"}).encode()
    _tok=json.load(urllib.request.urlopen(urllib.request.Request(f"{ISSUER}/oauth2/v1/token",
      data=d,headers={"Content-Type":"application/x-www-form-urlencoded"}),timeout=30))["access_token"]
    p=_tok.split(".")[1]; c=json.loads(base64.urlsafe_b64decode(p+"="*(-len(p)%4)))
    _cust=next(s["tnt"] for s in c["service-info"] if s.get("prd")=="ZPA")
    return _tok,_cust
def req(method,path,body=None,ver="v1"):
    tok,cust=_auth()
    url=f"{GW}/zpa/mgmtconfig/{ver}/admin/customers/{cust}/{path}"
    data=json.dumps(body).encode() if body is not None else None
    h={"Authorization":f"Bearer {tok}"}
    if data: h["Content-Type"]="application/json"
    r=urllib.request.Request(url,data=data,headers=h,method=method)
    try:
        with urllib.request.urlopen(r,timeout=45) as resp:
            raw=resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"_body":e.read()[:400].decode("utf8","replace")}
def get(path,ver="v1"): return req("GET",path,None,ver)
