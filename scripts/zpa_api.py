"""Minimal ZPA OneAPI client. Standalone: no dependencies outside the stdlib and no coupling
to anything else in the tenant.

    from zpa_api import get, req, zpa_list, one, redact

    get(path, ver="v1")                  -> (http_status, parsed_json_or_{})
    req(method, path, body=None, ver)    -> same; non-GET honours DRY_RUN and ZCC_READ_ONLY
    zpa_list(path, ver="v1")             -> every item of a list resource (follows pages)
    one(items, name, what)               -> exactly one item of that name, None, or exit 4 on two

Auth is the bundle Switchboard mounts (ZS_ISSUER, ZS_CLIENT_ID, optional ZS_GATEWAY and
ZPA_CUSTOMER_ID, client secret in ZS_API_KEY_FILE or ~/.zscaler_api_key). The bearer token
lives in memory only and is never printed.

Safety model (ported from the Cloud Connector lab's oneapi.py, same semantics):
  * `DRY_RUN = True` turns every non-GET into a printed "WOULD <METHOD> <path>" line and
    returns (0, {}) so callers keep going. prune.py sets it unless --apply is given.
  * `ZCC_READ_ONLY=1` in the environment is a hard lock: any non-GET exits the process with
    status 3 before a request is built. Set it while building or auditing.
  * Error bodies are truncated and passed through `redact()` so an API error that echoes a
    request cannot leak a provisioning key into a log.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ISSUER = os.environ.get("ZS_ISSUER", "https://YOUR_TENANT.zslogin.net")
GW = os.environ.get("ZS_GATEWAY", "https://api.zsapi.net").rstrip("/")
CID = os.environ.get("ZS_CLIENT_ID", "")
KEY_FILE = os.environ.get("ZS_API_KEY_FILE") or os.environ.get("ZSCALER_API_KEY_FILE") or os.path.expanduser("~/.zscaler_api_key")

DRY_RUN = False
READ_ONLY_LOCK = os.environ.get("ZCC_READ_ONLY", "") not in ("", "0", "false", "no")

_tok = None
_tok_exp = 0.0
_cust = os.environ.get("ZPA_CUSTOMER_ID") or None

_SECRET_KEY = re.compile(r"(key|secret|password|passwd|token|provisioningkey)", re.I)
_SECRET_KEY_ALLOW = re.compile(r"(^id$|Id$|^keyId$|Name$|Count$|Time$|Type$|Status$)")


def redact(obj, key=""):
    """Return a copy of obj with any value under a secret-looking key replaced by its length."""
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, key) for v in obj]
    if isinstance(obj, str) and key and _SECRET_KEY.search(key) and not _SECRET_KEY_ALLOW.search(key):
        return f"<redacted len={len(obj)}>"
    return obj


def _auth():
    global _tok, _tok_exp, _cust
    if _tok and time.time() < _tok_exp - 60:
        return _tok, _cust
    sec = open(KEY_FILE).read().strip()
    d = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": CID,
                                "client_secret": sec, "audience": "https://api.zscaler.com"}).encode()
    del sec
    body = json.load(urllib.request.urlopen(urllib.request.Request(
        f"{ISSUER}/oauth2/v1/token", data=d, headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=30))
    _tok = body["access_token"]
    p = _tok.split(".")[1]
    c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    _tok_exp = float(c.get("exp") or (time.time() + int(body.get("expires_in", 3000))))
    if not _cust:
        _cust = next(s["tnt"] for s in c["service-info"] if s.get("prd") == "ZPA")
    return _tok, _cust


def req(method, path, body=None, ver="v1"):
    method = method.upper()
    if method != "GET":
        if DRY_RUN:
            print(f"  WOULD {method} zpa:{path}" + (f"  body={json.dumps(redact(body))[:600]}" if body is not None else ""))
            return 0, {}
        if READ_ONLY_LOCK:
            print(f"zpa_api: ZCC_READ_ONLY is set; refusing {method} zpa:{path}", file=sys.stderr)
            sys.exit(3)
    tok, cust = _auth()
    url = f"{GW}/zpa/mgmtconfig/{ver}/admin/customers/{cust}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    if data:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=45) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            if e.code == 429 and attempt < 3:
                time.sleep(2 * (attempt + 1) + int(e.headers.get("Retry-After", "0") or 0))
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {"_body": raw[:400].decode("utf8", "replace")}
            return e.code, redact(parsed)
        except OSError as e:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            return 0, {"_error": f"{e.__class__.__name__}: {e}"}
    return 0, {"_error": "unreachable"}


def get(path, ver="v1"):
    return req("GET", path, None, ver)


def zpa_list(path, ver="v1", pagesize=500):
    """All items of a list resource. Exits non-zero on HTTP error."""
    items, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        code, body = get(f"{path}{sep}pagesize={pagesize}&page={page}", ver)
        if code != 200:
            print(f"zpa_api: GET {path} HTTP {code}: {json.dumps(body)[:300]}", file=sys.stderr)
            sys.exit(2)
        chunk = body.get("list") or []
        items.extend(chunk)
        total = int(body.get("totalPages") or 1)
        if page >= total or not chunk:
            return items
        page += 1


def one(items, name, what, key="name"):
    """Exactly one item whose `key` equals `name`, or None when absent. Two or more = exit 4:
    a duplicated lab name means somebody created something by hand; never guess."""
    hits = [i for i in items if isinstance(i, dict) and str(i.get(key)) == str(name)]
    if len(hits) > 1:
        print(f"AMBIGUOUS: {len(hits)} {what} named {name!r} (ids {[h.get('id') for h in hits]}); "
              f"resolve by hand in the portal before rerunning", file=sys.stderr)
        sys.exit(4)
    return hits[0] if hits else None
