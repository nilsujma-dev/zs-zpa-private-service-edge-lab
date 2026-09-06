"""Stdlib-only AWS SigV4 caller (the PSE lab's helper, extended).

    call(service, host, region, params)          Query-protocol POST (EC2, Service Quotas is JSON — use jcall)
    jcall(service, host, region, target, body)   JSON 1.1 X-Amz-Target POST (SSM, Secrets Manager, Service Quotas)
    creds()                                      access key / secret / optional session token

Credentials come from the environment (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, optional
AWS_SESSION_TOKEN) or, failing that, the profile in ~/.aws/credentials named by AWS_PROFILE
(default `default`). Nothing here prints or writes a credential; callers get a dict and the
signature. `call` returns the raw response text (XML for EC2) or "HTTP <code>: <body>" on
error, exactly as the PSE lab's version did. `jcall` returns parsed JSON or
{"_err": <code>, "_body": <text>} — callers must check `_err`.
"""
import configparser
import datetime
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class NoCredentials(RuntimeError):
    pass


def creds():
    ak = os.environ.get("AWS_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
    st = os.environ.get("AWS_SESSION_TOKEN")
    if not (ak and sk):
        path = os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or os.path.expanduser("~/.aws/credentials")
        prof = os.environ.get("AWS_PROFILE", "default")
        cp = configparser.ConfigParser()
        if cp.read(path) and cp.has_section(prof):
            ak = cp.get(prof, "aws_access_key_id", fallback=None)
            sk = cp.get(prof, "aws_secret_access_key", fallback=None)
            st = cp.get(prof, "aws_session_token", fallback=None)
    if not (ak and sk):
        raise NoCredentials("no AWS credentials in the environment or ~/.aws/credentials")
    return {"ak": ak, "sk": sk, "st": st}


def have_creds():
    try:
        creds()
        return True
    except NoCredentials:
        return False


def _sign(c, service, region, host, payload, ct, extra_headers):
    t = datetime.datetime.now(datetime.timezone.utc)
    ad = t.strftime("%Y%m%dT%H%M%SZ")
    ds = t.strftime("%Y%m%d")
    hdrs = {"content-type": ct, "host": host, "x-amz-date": ad}
    if c["st"]:
        hdrs["x-amz-security-token"] = c["st"]
    hdrs.update({k.lower(): v for k, v in extra_headers.items()})
    signed = ";".join(sorted(hdrs))
    canon_h = "".join(f"{k}:{hdrs[k]}\n" for k in sorted(hdrs))
    ph = hashlib.sha256(payload.encode()).hexdigest()
    canon = f"POST\n/\n\n{canon_h}\n{signed}\n{ph}"
    scope = f"{ds}/{region}/{service}/aws4_request"
    s2s = f"AWS4-HMAC-SHA256\n{ad}\n{scope}\n" + hashlib.sha256(canon.encode()).hexdigest()

    def _s(k, m):
        return hmac.new(k, m.encode(), hashlib.sha256).digest()

    k = _s(_s(_s(_s(("AWS4" + c["sk"]).encode(), ds), region), service), "aws4_request")
    sig = hmac.new(k, s2s.encode(), hashlib.sha256).hexdigest()
    out = {"Content-Type": ct, "X-Amz-Date": ad,
           "Authorization": f"AWS4-HMAC-SHA256 Credential={c['ak']}/{scope}, SignedHeaders={signed}, Signature={sig}"}
    if c["st"]:
        out["X-Amz-Security-Token"] = c["st"]
    out.update(extra_headers)
    return out


def call(service, host, region, params, timeout=25):
    c = creds()
    payload = urllib.parse.urlencode(params)
    ct = "application/x-www-form-urlencoded; charset=utf-8"
    headers = _sign(c, service, region, host, payload, ct, {})
    req = urllib.request.Request(f"https://{host}/", data=payload.encode(), headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=timeout).read().decode()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode()[:500]}"


def jcall(service, host, region, target, body, timeout=25):
    c = creds()
    payload = json.dumps(body)
    ct = "application/x-amz-json-1.1"
    headers = _sign(c, service, region, host, payload, ct, {"X-Amz-Target": target})
    req = urllib.request.Request(f"https://{host}/", data=payload.encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_body": e.read()[:400].decode("utf8", "replace")}


if __name__ == "__main__":
    import re
    import sys
    reg = sys.argv[1] if len(sys.argv) > 1 else "eu-central-1"
    print(f"### REGION {reg} ###")
    r = call("ec2", f"ec2.{reg}.amazonaws.com", reg, {"Action": "DescribeVpcs", "Version": "2016-11-15"})
    ids = re.findall(r"<vpcId>(.*?)</vpcId>", r)
    cidr = re.findall(r"<cidrBlock>(.*?)</cidrBlock>", r)
    print("VPCs:", list(zip(ids, cidr)) if ids else r[:300])
