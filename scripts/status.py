"""Lab status: EC2 health and ZPA enrolment for the AWS-Lab groups.

    python3 scripts/status.py                   human-readable
    python3 scripts/status.py --json            one JSON object on stdout (Switchboard's status probe)
    python3 scripts/status.py --json --no-aws   tenant only; instances empty, stale counted without the instance clause

Resolves the AWS-Lab groups by NAME rather than from zpa-created.json, so it works
from a fresh checkout that has never run zpa_create.py -- e.g. a control plane
inspecting a lab that was built elsewhere.

Output shape:

  {healthy, summary, region, components: [...], instances: [...],
   stale: {count, connectors, service_edges, cc_vms, cc_groups, locations, last_prune, last_prune_deleted},
   keys: [{type, name, usage, max, current}]}

`stale` is the flat count of what prune.py would delete right now (same rule: prune_lib.py;
the instance clause uses the EC2 list when AWS data is present, else entries are counted with
`aws_checked: false`). `summary` carries ", N stale entries" when N > 0. `keys` lists the lab's
provisioning keys (`current` = highest version of its family). `healthy` ignores both.
"""
import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import prune_lib  # noqa: E402
from aws_call import call, have_creds  # noqa: E402
from zpa_api import zpa_list  # noqa: E402

NS = "{http://ec2.amazonaws.com/doc/2016-11-15/}"
REG = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
AUTH = "ZPN_STATUS_AUTHENTICATED"
PROJECT_TAG = "zpa-pse-lab"
PRUNE_STATE = os.path.join(here, "prune-last.json")

COMPONENTS = (
    ("pse", "Private Service Edge", "serviceEdgeGroup", "AWS-Lab PSE Group"),
    ("connector_vpc_a", "App Connector (VPC A)", "appConnectorGroup", "AWS-Lab App Connector Group"),
    ("connector_priv", "App Connector (PRIV)", "appConnectorGroup", "AWS-Lab PRIV Connector Group"),
)


def zpa_components(instances, aws_checked):
    """One entry per expected component, enrolled or not; plus the stale counts and key table."""
    se_groups = zpa_list("serviceEdgeGroup")
    cg_groups = zpa_list("appConnectorGroup")
    se_idx = {g["name"]: str(g["id"]) for g in se_groups}
    cg_idx = {g["name"]: str(g["id"]) for g in cg_groups}
    wanted = {}
    for cid, label, kind, gname in COMPONENTS:
        gid = (se_idx if kind == "serviceEdgeGroup" else cg_idx).get(gname)
        wanted[cid] = {"id": cid, "label": label, "group": gname, "group_id": gid,
                       "authenticated": False, "control_channel": None, "version": None,
                       "private_ip": None, "public_ip": None, "enrolled_as": None}

    def fill(cid, rec):
        cur = wanted[cid]
        if cur["authenticated"] and rec.get("controlChannelStatus") != AUTH:
            return                      # never let a stale record overwrite the live one
        cur.update(
            authenticated=rec.get("controlChannelStatus") == AUTH,
            control_channel=rec.get("controlChannelStatus"),
            version=rec.get("currentVersion"),
            private_ip=rec.get("privateIp"),
            public_ip=rec.get("publicIp"),
            enrolled_as=rec.get("name"))

    now = time.time()
    stale = {"count": 0, "connectors": 0, "service_edges": 0, "cc_vms": 0, "cc_groups": 0, "locations": 0,
             "last_prune": None, "last_prune_deleted": None, "aws_checked": bool(aws_checked)}
    keys = []
    try:
        groups = prune_lib.resolve_lab_groups(cg_groups, se_groups, [c[3] for c in COMPONENTS if c[2] == "appConnectorGroup"], COMPONENTS[0][3])
    except prune_lib.Refuse as e:
        groups = {"cg": {}, "se": {}}
        stale["error"] = str(e)
    edges = sorted(zpa_list("serviceEdge"), key=lambda r: int(r.get("creationTime") or 0))
    for rec in edges:
        if str(rec.get("serviceEdgeGroupId")) == wanted["pse"]["group_id"]:
            fill("pse", rec)
        stale["service_edges"] += prune_lib.classify_zpa_entry(rec, "serviceEdge", groups["se"], now, instances, aws_checked=aws_checked)[0] == "PRUNE"
    conns = sorted(zpa_list("connector"), key=lambda r: int(r.get("creationTime") or 0))
    for rec in conns:
        gid = str(rec.get("appConnectorGroupId"))
        for cid in ("connector_vpc_a", "connector_priv"):
            if gid == wanted[cid]["group_id"]:
                fill(cid, rec)
        stale["connectors"] += prune_lib.classify_zpa_entry(rec, "connector", groups["cg"], now, instances, aws_checked=aws_checked)[0] == "PRUNE"
    stale["count"] = stale["connectors"] + stale["service_edges"]
    try:
        last = json.load(open(PRUNE_STATE))
        stale["last_prune"], stale["last_prune_deleted"] = last.get("at"), last.get("deleted")
    except (OSError, ValueError):
        pass
    for assoc, lab_ids in (("CONNECTOR_GRP", groups["cg"]), ("SERVICE_EDGE_GRP", groups["se"])):
        mine = prune_lib.lab_keys(zpa_list(f"associationType/{assoc}/provisioningKey"), assoc, lab_ids)
        for k in mine:
            b, _ = prune_lib.key_family(k.get("name"))
            keys.append({"type": assoc, "name": k.get("name"), "usage": k.get("usageCount"), "max": k.get("maxUsage"),
                         "current": prune_lib.current_key(mine, b, k.get("zcomponentId")) is k})
    return list(wanted.values()), stale, keys


def ec2_instances():
    raw = call("ec2", f"ec2.{REG}.amazonaws.com", REG, {
        "Action": "DescribeInstances", "Version": "2016-11-15",
        "Filter.1.Name": "tag:Project", "Filter.1.Value.1": PROJECT_TAG,
        "Filter.2.Name": "instance-state-name",
        "Filter.2.Value.1": "pending", "Filter.2.Value.2": "running",
        "Filter.2.Value.3": "stopping", "Filter.2.Value.4": "stopped"})
    if raw.startswith("HTTP"):
        return [], raw[:120]
    root = ET.fromstring(raw)
    out = []
    for inst in root.iter(f"{NS}instancesSet"):
        for it in inst.findall(f"{NS}item"):
            name = next((t.findtext(f"{NS}value") for t in it.findall(f".//{NS}tagSet/{NS}item")
                         if t.findtext(f"{NS}key") == "Name"), None)
            ips = [x.text for x in it.iter(f"{NS}privateIpAddress")]
            out.append({"id": it.findtext(f"{NS}instanceId"), "name": name,
                        "state": it.findtext(f".//{NS}instanceState/{NS}name"),
                        "type": it.findtext(f"{NS}instanceType"),
                        "private_ip": it.findtext(f"{NS}privateIpAddress"),
                        "private_ips": sorted({i for i in ips if i}),
                        "public_ip": it.findtext(f"{NS}ipAddress")})
    return sorted(out, key=lambda x: x["name"] or ""), None


def snapshot(no_aws=False):
    inst, err = ([], "AWS skipped") if no_aws or not have_creds() else ec2_instances()
    aws_checked = not (no_aws or err)
    comps, stale, keys = zpa_components(inst if aws_checked else None, aws_checked)
    return {
        "healthy": all(c["authenticated"] for c in comps) and any(i["state"] == "running" for i in inst),
        "summary": f"{sum(c['authenticated'] for c in comps)}/{len(comps)} components authenticated, "
                   f"{sum(i['state'] == 'running' for i in inst)}/{len(inst)} instances running"
                   + (f", {stale['count']} stale entries" if stale["count"] else ""),
        "components": comps,
        "instances": inst,
        "region": REG,
        "stale": stale,
        "keys": keys,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit one JSON object")
    ap.add_argument("--no-aws", action="store_true", help="tenant only (no EC2 call)")
    args = ap.parse_args()
    snap = snapshot(args.no_aws)
    if args.json:
        print(json.dumps(snap))
        return
    print(f"=== EC2 ({REG}) ===")
    for i in snap["instances"] or [{"name": "-", "id": "(no AWS data)", "type": "", "state": "", "private_ip": ""}]:
        print(f"  {i['name'] or '-':24} {i['id']:20} {i['type'] or '':10} {i['state'] or '':9} {i['private_ip'] or ''}")
    print("\n=== ZPA enrolment ===")
    for c in snap["components"]:
        mark = "OK " if c["authenticated"] else "-- "
        print(f"  {mark}{c['label']:24} {c['control_channel'] or 'not enrolled':28} {c['version'] or ''}")
    if snap["stale"]["count"]:
        print(f"\n  stale (./lab.sh prune to list, --apply to delete): {json.dumps(snap['stale'])}")
    for k in snap["keys"]:
        print(f"  key {k['type']:17} {k['name']!r:42} {k['usage']}/{k['max']}{'  current' if k['current'] else ''}")
    print(f"\n{snap['summary']}")


if __name__ == "__main__":
    main()
