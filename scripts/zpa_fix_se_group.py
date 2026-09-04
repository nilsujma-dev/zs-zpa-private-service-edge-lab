"""Align the AWS-Lab Service Edge Group's enrollmentCertId with the tenant's
working group (31030 Connector), leaving the provisioning key on 31031.

This is the ONLY update this project performs. It is hard-guarded to the single
group id created by this automation; it refuses to touch anything else.
"""
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from zpa_api import req, get

made = json.load(open(os.path.join(here, "zpa-created.json")))
TARGET = str(made["serviceEdgeGroupId"])
PROTECTED = {"72063920524755031"}          # the production ZPA PSE Group
WANT_CERT = "31030"
WANT_PUBLIC = "TRUE"

if TARGET in PROTECTED:
    print("REFUSING: target is a protected pre-existing group")
    sys.exit(1)

c, cur = get(f"serviceEdgeGroup/{TARGET}")
if c != 200:
    print(f"fetch failed HTTP {c}")
    sys.exit(1)
if cur.get("name") != "AWS-Lab PSE Group":
    print(f"REFUSING: id {TARGET} is named {cur.get('name')!r}, not the AWS-Lab group")
    sys.exit(1)

print(f"target   : {TARGET}  {cur.get('name')}")
print(f"before   : enrollmentCertId={cur.get('enrollmentCertId')} isPublic={cur.get('isPublic')}")

body = {k: v for k, v in cur.items()
        if k not in ("modifiedTime", "creationTime", "modifiedBy", "serviceEdges",
                     "version", "versionProfileName", "cityCountry", "countryCode")}
body["enrollmentCertId"] = WANT_CERT
body["isPublic"] = WANT_PUBLIC
body.pop("signingCertId", None)   # sending it overrides enrollmentCertId

c, j = req("PUT", f"serviceEdgeGroup/{TARGET}", body)
print(f"PUT      : HTTP {c}")
if c not in (200, 201, 204):
    print(json.dumps(j)[:400])
    sys.exit(1)

c, after = get(f"serviceEdgeGroup/{TARGET}")
print(f"after    : enrollmentCertId={after.get('enrollmentCertId')} isPublic={after.get('isPublic')}")
print("changed" if after.get("enrollmentCertId") != cur.get("enrollmentCertId") else "NO CHANGE")

# prove the production group is untouched
c, prod = get("serviceEdgeGroup/72063920524755031")
print(f"prod grp : {prod.get('name')} enrollmentCertId={prod.get('enrollmentCertId')} "
      f"(expected 31030, unchanged)")
