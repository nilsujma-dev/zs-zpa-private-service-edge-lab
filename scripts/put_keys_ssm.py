import os
"""ZPA provisioning key -> SSM SecureString.

The key value is held in memory only: never written to disk, never printed,
never placed in EC2 user_data, and therefore never captured in OpenTofu state.
Each instance reads its own parameter at boot via its IAM role.
"""
import datetime, hashlib, hmac, json, os, sys, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zpa_api import get

REG = "eu-central-1"


def ssm(target, body):
    AK = os.environ["AWS_ACCESS_KEY_ID"]
    SK = os.environ["AWS_SECRET_ACCESS_KEY"]
    ST = os.environ["AWS_SESSION_TOKEN"]
    host = f"ssm.{REG}.amazonaws.com"
    payload = json.dumps(body)
    t = datetime.datetime.now(datetime.timezone.utc)
    ad = t.strftime("%Y%m%dT%H%M%SZ")
    ds = t.strftime("%Y%m%d")
    ph = hashlib.sha256(payload.encode()).hexdigest()
    ct = "application/x-amz-json-1.1"
    sh = "content-type;host;x-amz-date;x-amz-security-token;x-amz-target"
    canon = (f"POST\n/\n\ncontent-type:{ct}\nhost:{host}\nx-amz-date:{ad}\n"
             f"x-amz-security-token:{ST}\nx-amz-target:{target}\n\n{sh}\n{ph}")
    sc = f"{ds}/{REG}/ssm/aws4_request"
    s2s = f"AWS4-HMAC-SHA256\n{ad}\n{sc}\n" + hashlib.sha256(canon.encode()).hexdigest()

    def _s(k, m):
        return hmac.new(k, m.encode(), hashlib.sha256).digest()

    k = _s(_s(_s(_s(("AWS4" + SK).encode(), ds), REG), "ssm"), "aws4_request")
    sig = hmac.new(k, s2s.encode(), hashlib.sha256).hexdigest()
    r = urllib.request.Request(f"https://{host}/", data=payload.encode(), headers={
        "Content-Type": ct, "X-Amz-Date": ad, "X-Amz-Security-Token": ST,
        "X-Amz-Target": target,
        "Authorization": f"AWS4-HMAC-SHA256 Credential={AK}/{sc}, "
                         f"SignedHeaders={sh}, Signature={sig}"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_body": e.read()[:300].decode("utf8", "replace")}


here = os.path.dirname(os.path.abspath(__file__))
made = json.load(open(os.path.join(here, "zpa-created.json")))

for assoc, slot, param in (
        ("SERVICE_EDGE_GRP", "pse", "/zpa-lab/pse-provisioning-key"),
        ("CONNECTOR_GRP", "conn", "/zpa-lab/connector-provisioning-key"),
        ("CONNECTOR_GRP", "privConn", "/zpa-lab/priv-connector-provisioning-key")):
    code, j = get(f"associationType/{assoc}/provisioningKey")
    if code != 200:
        print(f"{assoc}: ZPA HTTP {code}")
        sys.exit(1)
    row = next((i for i in j["list"] if str(i["id"]) == made[slot + "KeyId"]), None)
    if not row:
        print(f"{assoc}: key {made[slot + 'KeyId']} not found")
        sys.exit(1)
    val = row.get("provisioningKey") or ""
    if not val:
        print(f"{assoc}: empty key value")
        sys.exit(1)
    sc, sj = ssm("AmazonSSM.PutParameter",
                 {"Name": param, "Value": val, "Type": "SecureString", "Overwrite": True})
    print(f"{assoc:17} key id={row['id']} len={len(val)} -> {param}  "
          f"HTTP {sc} version={sj.get('Version')}")
    if sc != 200:
        print("   ", json.dumps(sj)[:250])
        sys.exit(1)

print("\nBoth keys stored as SecureString. Values not written to disk or printed.")
