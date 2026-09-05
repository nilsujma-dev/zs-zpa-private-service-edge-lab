"""Adds the PRIV App Connector Group + provisioning key. Create-only, resume-safe.
Never issues PUT or DELETE. Never touches an object it did not create."""
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from zpa_api import req, get

PREFIX = "AWS-Lab"
LAT, LON, LOC = "50.1109221", "8.6821267", "Frankfurt am Main, Germany"
ROOT, CERT_CONN = "31028", "31030"
CG_NAME = f"{PREFIX} PRIV Connector Group"

made = json.load(open(os.path.join(here, "zpa-created.json")))


def index(res):
    c, j = get(res)
    return {i["name"]: i["id"] for i in (j.get("list") or [])} if c == 200 else {}


before = index("appConnectorGroup")
print(f"connector groups before: {len(before)}")

if CG_NAME in before:
    gid = before[CG_NAME]
    print(f"  REUSE  id={gid}  {CG_NAME}")
else:
    c, j = req("POST", "appConnectorGroup", {
        "name": CG_NAME,
        "description": "Standalone AWS lab PRIV connector. Serves the lab nginx server only.",
        "enabled": True, "latitude": LAT, "longitude": LON, "location": LOC,
        "upgradeDay": "SUNDAY", "upgradeTimeInSecs": "66600", "dnsQueryType": "IPV4_IPV6",
        "signingCertId": ROOT, "enrollmentCertId": CERT_CONN,
        "overrideVersionProfile": True, "versionProfileId": "0"})
    if c not in (200, 201):
        print(f"  HTTP {c}: {json.dumps(j)[:300]}")
        sys.exit(1)
    gid = j["id"]
    print(f"  CREATE id={gid}  {j['name']}")
made["privConnectorGroupId"] = gid

path = "associationType/CONNECTOR_GRP/provisioningKey"
keys = index(path)
kn = f"{PREFIX} PRIV CONNECTOR_GRP key"
if kn in keys:
    print(f"  provisioningKey REUSE  id={keys[kn]}")
    made["privConnKeyId"] = keys[kn]
else:
    c, j = req("POST", path, {"name": kn, "maxUsage": "5", "enrollmentCertId": CERT_CONN,
                              "zcomponentId": str(gid), "enabled": True, "signingCertId": ROOT})
    if c not in (200, 201):
        print(f"  provisioningKey HTTP {c}: {json.dumps(j)[:300]}")
        sys.exit(1)
    print(f"  provisioningKey CREATE id={j['id']}")
    made["privConnKeyId"] = j["id"]

json.dump(made, open(os.path.join(here, "zpa-created.json"), "w"), indent=2)
after = index("appConnectorGroup")
print(f"connector groups after: {len(after)}")
print("pre-existing preserved:", set(before) <= set(after))
print(json.dumps(made, indent=2))
