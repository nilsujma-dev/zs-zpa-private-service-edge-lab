import os
# Read-only ZPA discovery via OneAPI. GET only. Standalone and side-effect free.
import base64,json,os,sys,urllib.parse,urllib.request,urllib.error
ISSUER=os.environ.get("ZS_ISSUER","https://YOUR_TENANT.zslogin.net"); GW="https://api.zsapi.net"; CID=os.environ.get("ZS_CLIENT_ID","")
sec=open(os.path.expanduser("~/.zscaler_api_key")).read().strip()
d=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":CID,
  "client_secret":sec,"audience":"https://api.zscaler.com"}).encode()
tok=json.load(urllib.request.urlopen(urllib.request.Request(f"{ISSUER}/oauth2/v1/token",data=d,
  headers={"Content-Type":"application/x-www-form-urlencoded"}),timeout=25))["access_token"]
p=tok.split(".")[1]; claims=json.loads(base64.urlsafe_b64decode(p+"="*(-len(p)%4)))
cust=next(s["tnt"] for s in claims["service-info"] if s.get("prd")=="ZPA")
print("ZPA customer id:",cust,"cloud:",next(s.get("cld") for s in claims["service-info"] if s.get("prd")=="ZPA"),"\n")
def get(res):
    u=f"{GW}/zpa/mgmtconfig/v1/admin/customers/{cust}/{res}?pagesize=500"
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,headers={"Authorization":f"Bearer {tok}"}),timeout=30)
        return json.load(r)
    except urllib.error.HTTPError as e: return {"_http":e.code,"_body":e.read()[:200].decode("utf8","replace")}
for res in ("serviceEdgeGroup","appConnectorGroup","enrollmentCert"):
    j=get(res)
    if "_http" in j: print(f"{res}: HTTP {j['_http']} {j['_body']}"); continue
    items=j.get("list",[]); print(f"{res}: {j.get('totalCount','?')} item(s)")
    for it in items[:12]:
        bits=[f"  - {it.get('name')}"]
        for k in ("latitude","longitude","cityCountry","enabled","upgradeDay","numConnectors","numServiceEdges"):
            if it.get(k) not in (None,""): bits.append(f"{k}={it[k]}")
        print(" ".join(bits))
    print()
