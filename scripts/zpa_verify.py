"""Prove no pre-existing ZPA object was modified. Compares live state to the pre-write baseline.

    python3 scripts/zpa_verify.py [--snapshot zpa-snapshot]

What "this lab owns" (excluded on BOTH sides, listed as lab-owned): anything named `AWS-Lab*`
and any id in scripts/zpa-created.json. Lab-owned groups are compared by IDENTITY, never by
member count: the `connectors` / `serviceEdges` lists inside a group, `usageCount` on a key
and the timestamps are volatile, so a lab group with fewer entries (or none) after prune.py,
and a lab key raised to maxUsage 200 or rotated, are not differences. Production objects are
compared scrubbed the same way, so a heartbeat is not a change either. Provisioning key
values are never hashed or written.

Exit 0 = unchanged, 1 = difference detected, 2 = baseline missing.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from zpa_api import get, redact  # noqa: E402

PREFIX = "AWS-Lab"
VOLATILE = {"modifiedTime", "modifiedBy", "creationTime", "connectors", "serviceEdges", "usageCount",
            "version", "versionProfileName", "provisioningKey"}


def scrub(obj, key=""):
    if isinstance(obj, dict):
        return {k: scrub(v, k) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [scrub(v, key) for v in obj]
    return redact(obj, key)


def digest(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:16]


def lab_owned(item, mine):
    return str(item.get("name") or "").startswith(PREFIX) or str(item.get("id")) in mine


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=os.path.join(HERE, "..", "zpa-snapshot"))
    args = ap.parse_args()
    snapdir = os.path.abspath(args.snapshot)
    mpath = os.path.join(snapdir, "MANIFEST.json")
    if not os.path.exists(mpath):
        print(f"no baseline at {mpath}: run zpa_snapshot.py first", file=sys.stderr)
        sys.exit(2)
    base = json.load(open(mpath))["objects"]
    created = os.path.join(HERE, "zpa-created.json")
    mine = set(str(v) for v in json.load(open(created)).values()) if os.path.exists(created) else set()
    ok = True
    for res, b in base.items():
        if not isinstance(b, dict):
            continue
        c, j = get(f"{res}?pagesize=500")
        if c != 200:
            print(f"{res:46} live fetch HTTP {c}")
            ok = False
            continue
        live = scrub(j.get("list") or [])
        surviving = [i for i in live if not lab_owned(i, mine)]
        new = [i for i in live if lab_owned(i, mine)]
        ids_now = sorted(str(i.get("id")) for i in surviving)
        fpath = os.path.join(snapdir, res.replace("/", "_") + ".json")
        try:
            base_items = scrub(json.load(open(fpath)).get("list") or [])
        except (OSError, ValueError):
            base_items = None
        if base_items is not None:
            base_surv = [i for i in base_items if not lab_owned(i, mine)]
            base_ids, base_hash = sorted(str(i.get("id")) for i in base_surv), digest(base_surv)
        else:
            base_ids, base_hash = b["ids"], None       # legacy manifest without the per-resource file: ids only
        same_ids = ids_now == base_ids
        same_hash = base_hash is None or digest(surviving) == base_hash
        flag = "UNCHANGED" if (same_ids and same_hash) else ("IDS OK, CONTENT DIFFERS" if same_ids else "MISMATCH")
        if base_hash is None and same_ids:
            flag = "IDS UNCHANGED (no content file)"
        if not (same_ids and same_hash):
            ok = False
        print(f"{res:46} {b['count']:3} before -> {len(surviving):3} pre-existing + {len(new)} lab-owned   {flag}")
        if not same_ids:
            print(f"    baseline ids: {base_ids}\n    live ids    : {ids_now}")
    print()
    print("VERDICT:", "no pre-existing object was added, removed or modified" if ok
          else "*** DIFFERENCE DETECTED -- investigate before proceeding ***")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
