"""Create-only, resume-safe. Reuses AWS-Lab objects if already made.
Never issues PUT or DELETE. Never touches an object it did not create."""
import json,sys
sys.path.insert(0,'.')
from zpa_api import req,get

PREFIX="AWS-Lab"; LAT,LON,LOC="50.1109221","8.6821267","Frankfurt am Main, Germany"
ROOT,CERT_CONN,CERT_SE = "31028","31030","31031"
SE_NAME=f"{PREFIX} PSE Group"; CG_NAME=f"{PREFIX} App Connector Group"
created={}

def index(res):
    c,j=get(res); return {i["name"]:i["id"] for i in (j.get("list") or [])} if c==200 else {}

def ensure(res,name,body):
    pool=index(res)
    if name in pool:
        print(f"  {res:20} REUSE  id={pool[name]}  {name}"); return pool[name]
    c,j=req("POST",res,body)
    if c not in (200,201):
        print(f"  {res:20} HTTP {c}: {json.dumps(j)[:300]}"); sys.exit(1)
    print(f"  {res:20} CREATE id={j['id']}  {j['name']}"); return j["id"]

se_before, cg_before = index("serviceEdgeGroup"), index("appConnectorGroup")
print(f"Before: {len(se_before)} service edge group(s), {len(cg_before)} connector group(s)\n")

created["serviceEdgeGroupId"]=ensure("serviceEdgeGroup",SE_NAME,{
    "name":SE_NAME,"description":"Standalone AWS lab PSE. Created by automation; belongs to no other deployment in this tenant.",
    "enabled":True,"latitude":LAT,"longitude":LON,"location":LOC,"isPublic":"false",
    "upgradeDay":"SUNDAY","upgradeTimeInSecs":"66600","signingCertId":ROOT,
    "enrollmentCertId":CERT_SE,"overrideVersionProfile":True,"versionProfileId":"0"})

created["appConnectorGroupId"]=ensure("appConnectorGroup",CG_NAME,{
    "name":CG_NAME,"description":"Isolated AWS lab App Connector, co-located with the lab PSE.",
    "enabled":True,"latitude":LAT,"longitude":LON,"location":LOC,
    "upgradeDay":"SUNDAY","upgradeTimeInSecs":"66600","dnsQueryType":"IPV4_IPV6",
    "signingCertId":ROOT,"enrollmentCertId":CERT_CONN,
    "overrideVersionProfile":True,"versionProfileId":"0"})

print()
for assoc,cert,zc,slot in (("SERVICE_EDGE_GRP",CERT_SE,created["serviceEdgeGroupId"],"pse"),
                           ("CONNECTOR_GRP",CERT_CONN,created["appConnectorGroupId"],"conn")):
    path=f"associationType/{assoc}/provisioningKey"
    pool=index(path); nm=f"{PREFIX} {assoc} key"
    if nm in pool:
        print(f"  provisioningKey {assoc:17} REUSE  id={pool[nm]}"); created[slot+"KeyId"]=pool[nm]; continue
    c,j=req("POST",path,{"name":nm,"maxUsage":"5","enrollmentCertId":cert,
                         "zcomponentId":str(zc),"enabled":True,"signingCertId":ROOT})
    if c not in (200,201):
        print(f"  provisioningKey {assoc:17} HTTP {c}: {json.dumps(j)[:300]}"); sys.exit(1)
    print(f"  provisioningKey {assoc:17} CREATE id={j['id']}"); created[slot+"KeyId"]=j["id"]
    created[slot+"KeyAssoc"]=assoc

open("zpa-created.json","w").write(json.dumps(created,indent=2))
se_after, cg_after = index("serviceEdgeGroup"), index("appConnectorGroup")
print(f"\nAfter: {len(se_after)} service edge group(s), {len(cg_after)} connector group(s)")
print("Pre-existing untouched:", set(se_before)|set(cg_before) <= set(se_after)|set(cg_after))
print(json.dumps(created,indent=2))
