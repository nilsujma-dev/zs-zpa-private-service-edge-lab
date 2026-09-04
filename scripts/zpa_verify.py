"""Prove no pre-existing ZPA object was modified. Compares live state to the pre-write baseline."""
import hashlib,json,sys
sys.path.insert(0,'.')
from zpa_api import get
base=json.load(open("zpa-snapshot/MANIFEST.json"))["objects"]
mine=set(json.load(open("zpa-created.json")).values())
ok=True
for res,b in base.items():
    if not isinstance(b,dict): continue
    c,j=get(res)
    if c!=200: print(f"{res:22} live fetch HTTP {c}"); ok=False; continue
    items=j.get("list") or []
    surviving=[i for i in items if str(i.get("id")) not in mine]
    ids_now=sorted(str(i.get("id")) for i in surviving)
    h=hashlib.sha256(json.dumps(surviving,sort_keys=True).encode()).hexdigest()[:16]
    added=[str(i.get('id')) for i in items if str(i.get("id")) in mine]
    same_ids = ids_now==b["ids"]
    same_hash = h==b["sha256_16"]
    flag = "UNCHANGED" if (same_ids and same_hash) else ("IDS OK, CONTENT DIFFERS" if same_ids else "MISMATCH")
    if not (same_ids and same_hash): ok=False
    print(f"{res:22} {b['count']:2} before -> {len(surviving):2} pre-existing + {len(added)} new   {flag}")
    if not same_ids:
        print(f"    baseline ids: {b['ids']}")
        print(f"    live ids    : {ids_now}")
print()
print("VERDICT:", "no pre-existing object was added, removed or modified" if ok
      else "*** DIFFERENCE DETECTED -- investigate before proceeding ***")
