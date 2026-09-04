import os
# READ-ONLY baseline of every ZPA object we could conceivably touch.
# Written before any create, so we can prove afterwards that nothing pre-existing moved.
import base64,hashlib,json,os,sys,urllib.parse,urllib.request,urllib.error
OUT=sys.argv[1]
ISSUER=os.environ.get("ZS_ISSUER","https://YOUR_TENANT.zslogin.net"); GW="https://api.zsapi.net"; CID=os.environ.get("ZS_CLIENT_ID","")
sec=open(os.path.expanduser("~/.zscaler_api_key")).read().strip()
d=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":CID,
  "client_secret":sec,"audience":"https://api.zscaler.com"}).encode()
tok=json.load(urllib.request.urlopen(urllib.request.Request(f"{ISSUER}/oauth2/v1/token",data=d,
  headers={"Content-Type":"application/x-www-form-urlencoded"}),timeout=30))["access_token"]
p=tok.split(".")[1]; claims=json.loads(base64.urlsafe_b64decode(p+"="*(-len(p)%4)))
cust=next(s["tnt"] for s in claims["service-info"] if s.get("prd")=="ZPA")
def get(res):
    u=f"{GW}/zpa/mgmtconfig/v1/admin/customers/{cust}/{res}?pagesize=500"
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(u,
            headers={"Authorization":f"Bearer {tok}"}),timeout=40))
    except urllib.error.HTTPError as e:
        return {"_http":e.code,"_body":e.read()[:200].decode("utf8","replace")}
manifest={}
for res in ("serviceEdgeGroup","appConnectorGroup","provisioningKey/association/CONNECTOR_GRP",
            "provisioningKey/association/SERVICE_EDGE_GRP","application","serverGroup","segmentGroup"):
    j=get(res); fn=res.replace("/","_")+".json"
    open(os.path.join(OUT,fn),"w").write(json.dumps(j,indent=2,sort_keys=True))
    if "_http" in j:
        manifest[res]=f"HTTP {j['_http']}"; print(f"  {res:46} HTTP {j['_http']}"); continue
    items=j.get("list",[]) or []
    ids=sorted(str(i.get("id")) for i in items)
    h=hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()[:16]
    manifest[res]={"count":len(items),"ids":ids,"sha256_16":h}
    print(f"  {res:46} {len(items):3} object(s)  sha={h}")
    for i in items[:8]: print(f"       id={i.get('id')}  {i.get('name')}")
open(os.path.join(OUT,"MANIFEST.json"),"w").write(json.dumps({"customer":cust,"objects":manifest},indent=2,sort_keys=True))
print("\ncustomer:",cust)
print("baseline written to",OUT)
