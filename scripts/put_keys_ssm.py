"""ZPA provisioning keys -> SSM SecureString, one parameter per component.

    python3 scripts/put_keys_ssm.py

Each key is resolved by FAMILY from the tenant: the highest `<family> v<N>` bound to the lab
group is current (prune.py mints v<N+1> when the headroom runs out and calls `seed()` here
with the new record). zpa-created.json, when present, is cross-checked and updated so the
id it carries is the current key's. The key value is held in memory only: never written to
disk, never printed, never placed in EC2 user_data, and therefore never captured in OpenTofu
state. Each instance reads its own parameter at boot via its IAM role.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prune_lib  # noqa: E402
from aws_call import have_creds, jcall  # noqa: E402
from zpa_api import DRY_RUN, one, zpa_list  # noqa: E402

REG = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
CREATED = os.path.join(HERE, "zpa-created.json")

# assoc, slot in zpa-created.json, group resource, group name, key family, SSM parameter
PARAMS = (
    ("SERVICE_EDGE_GRP", "pse", "serviceEdgeGroup", "AWS-Lab PSE Group", "AWS-Lab SERVICE_EDGE_GRP key", "/zpa-lab/pse-provisioning-key"),
    ("CONNECTOR_GRP", "conn", "appConnectorGroup", "AWS-Lab App Connector Group", "AWS-Lab CONNECTOR_GRP key", "/zpa-lab/connector-provisioning-key"),
    ("CONNECTOR_GRP", "privConn", "appConnectorGroup", "AWS-Lab PRIV Connector Group", "AWS-Lab PRIV CONNECTOR_GRP key", "/zpa-lab/priv-connector-provisioning-key"),
)


def param_for(assoc, family):
    return next((p for p in PARAMS if p[0] == assoc and p[4] == family), None)


def current_key(assoc, group_resource, group_name, family):
    """The current key record of one family, or None. Two of one name = stop."""
    grp = one(zpa_list(group_resource), group_name, group_resource)
    if not grp:
        return None
    keys = zpa_list(f"associationType/{assoc}/provisioningKey")
    cur = prune_lib.current_key(keys, family, grp["id"])
    if cur:
        one(keys, cur["name"], "provisioning keys")   # ambiguity check
    return cur


def seed(param, row, dry_run=None):
    """PutParameter the key's value (memory only). Returns (http_status, result); never the value."""
    dry_run = DRY_RUN if dry_run is None else dry_run
    val = row.get("provisioningKey") or ""
    if not val:
        print(f"  key id={row.get('id')} has an empty value", file=sys.stderr)
        return 0, {}
    if dry_run:
        print(f"  WOULD PutParameter {param} <- key id={row['id']} {row.get('name')!r} len={len(val)} (SecureString, Overwrite; value withheld)")
        return 0, {"dry_run": True}
    if not have_creds():
        print("no AWS credentials in env or ~/.aws/credentials", file=sys.stderr)
        return 0, {}
    r = jcall("ssm", f"ssm.{REG}.amazonaws.com", REG, "AmazonSSM.PutParameter",
              {"Name": param, "Value": val, "Type": "SecureString", "Overwrite": True})
    del val
    if "_err" in r:
        return r["_err"], r
    return 200, r


def record_created(slot, key_id):
    """Keep zpa-created.json (git-ignored, written by zpa_create.py) pointing at the current key."""
    if not os.path.exists(CREATED):
        return
    made = json.load(open(CREATED))
    if str(made.get(slot + "KeyId")) != str(key_id):
        made[slot + "KeyId"] = str(key_id)
        json.dump(made, open(CREATED, "w"), indent=2)


def main():
    made = json.load(open(CREATED)) if os.path.exists(CREATED) else {}
    for assoc, slot, gres, gname, family, param in PARAMS:
        row = current_key(assoc, gres, gname, family)
        if not row:
            print(f"{assoc:17} no key of the family {family!r} on {gname!r}: run zpa_create.py first")
            sys.exit(1)
        if int(row.get("usageCount") or 0) >= int(row.get("maxUsage") or 0):
            print(f"{assoc:17} key {row.get('name')!r} is exhausted ({row.get('usageCount')}/{row.get('maxUsage')}): run prune.py --apply to rotate")
            sys.exit(1)
        if made.get(slot + "KeyId") and str(made[slot + "KeyId"]) != str(row["id"]):
            print(f"{assoc:17} zpa-created.json pointed at key {made[slot + 'KeyId']}; current is {row['id']} ({row.get('name')!r}) -- updating")
        record_created(slot, row["id"])
        sc, sj = seed(param, row)
        print(f"{assoc:17} key id={row['id']} {row.get('name')!r} usage={row.get('usageCount')}/{row.get('maxUsage')} "
              f"cert={row.get('enrollmentCertName')} -> {param}  HTTP {sc} version={sj.get('Version')}")
        if sc != 200 and not DRY_RUN:
            print("   ", json.dumps(sj)[:250])
            sys.exit(1)
    print("\nKeys stored as SecureString. Values not written to disk or printed.")


if __name__ == "__main__":
    main()
