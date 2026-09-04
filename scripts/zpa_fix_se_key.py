"""Create a SERVICE_EDGE_GRP provisioning key with the correct enrollment cert.

The first key was created with both enrollmentCertId and signingCertId set, and
the API resolved enrollmentCertId to the signing cert (Root, 31028). Connectors
tolerate Root; the Service Edge does not -- it needs 31031 'Service Edge', which
is what the tenant's own working PSE key uses.

Create-only. The old key is left in place, disabled by nothing, simply unused.
"""
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from zpa_api import req, get

CERT_SE = "31031"
NAME = "AWS-Lab SERVICE_EDGE_GRP key v2"
made = json.load(open(os.path.join(here, "zpa-created.json")))
path = "associationType/SERVICE_EDGE_GRP/provisioningKey"

c, j = get(path)
existing = {i["name"]: i for i in (j.get("list") or [])} if c == 200 else {}

if NAME in existing:
    row = existing[NAME]
    print(f"REUSE id={row['id']} cert={row.get('enrollmentCertId')} "
          f"({row.get('enrollmentCertName')}) usage={row.get('usageCount')}/{row.get('maxUsage')}")
    made["pseKeyId"] = str(row["id"])
else:
    # NOTE: no signingCertId. Passing it is what corrupted the first key.
    c, j = req("POST", path, {
        "name": NAME,
        "maxUsage": "25",
        "enrollmentCertId": CERT_SE,
        "zcomponentId": str(made["serviceEdgeGroupId"]),
        "enabled": True})
    if c not in (200, 201):
        print(f"HTTP {c}: {json.dumps(j)[:400]}")
        sys.exit(1)
    print(f"CREATE id={j['id']}")
    made["pseKeyId"] = str(j["id"])

# verify the cert actually landed as requested
c, j = get(path)
row = next((i for i in j["list"] if str(i["id"]) == made["pseKeyId"]), None)
print(f"verify: enrollmentCertId={row.get('enrollmentCertId')} "
      f"({row.get('enrollmentCertName')})  maxUsage={row.get('maxUsage')}")
if str(row.get("enrollmentCertId")) != CERT_SE:
    print(f"FAIL: expected {CERT_SE}, got {row.get('enrollmentCertId')}")
    sys.exit(1)

json.dump(made, open(os.path.join(here, "zpa-created.json"), "w"), indent=2)
print("OK -- zpa-created.json now points at the corrected key")
