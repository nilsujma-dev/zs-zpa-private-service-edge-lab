"""Lab status: EC2 health and ZPA enrolment for the AWS-Lab groups.

    python3 scripts/status.py          human-readable
    python3 scripts/status.py --json   one JSON object on stdout (Switchboard's status probe)

Resolves the AWS-Lab groups by NAME rather than from zpa-created.json, so it works
from a fresh checkout that has never run zpa_create.py -- e.g. a control plane
inspecting a lab that was built elsewhere.
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from aws_call import call  # noqa: E402
from zpa_api import get  # noqa: E402

NS = "{http://ec2.amazonaws.com/doc/2016-11-15/}"
REG = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
AUTH = "ZPN_STATUS_AUTHENTICATED"
PROJECT_TAG = "zpa-pse-lab"

COMPONENTS = (
    ("pse", "Private Service Edge", "serviceEdgeGroup", "AWS-Lab PSE Group"),
    ("connector_vpc_a", "App Connector (VPC A)", "appConnectorGroup", "AWS-Lab App Connector Group"),
    ("connector_priv", "App Connector (PRIV)", "appConnectorGroup", "AWS-Lab PRIV Connector Group"),
)


def _index(resource):
    code, body = get(resource)
    if code != 200:
        return {}
    return {i["name"]: str(i["id"]) for i in (body.get("list") or [])}


def zpa_components():
    """One entry per expected component, enrolled or not."""
    se_groups = _index("serviceEdgeGroup")
    cg_groups = _index("appConnectorGroup")
    wanted = {}
    for cid, label, kind, gname in COMPONENTS:
        gid = (se_groups if kind == "serviceEdgeGroup" else cg_groups).get(gname)
        wanted[cid] = {"id": cid, "label": label, "group": gname, "group_id": gid,
                       "authenticated": False, "control_channel": None, "version": None,
                       "private_ip": None, "public_ip": None, "enrolled_as": None}

    def fill(cid, rec):
        wanted[cid].update(
            authenticated=rec.get("controlChannelStatus") == AUTH,
            control_channel=rec.get("controlChannelStatus"),
            version=rec.get("currentVersion"),
            private_ip=rec.get("privateIp"),
            public_ip=rec.get("publicIp"),
            enrolled_as=rec.get("name"))

    code, body = get("serviceEdge")
    for rec in (body.get("list") or []) if code == 200 else []:
        if str(rec.get("serviceEdgeGroupId")) == wanted["pse"]["group_id"]:
            fill("pse", rec)
    code, body = get("connector")
    for rec in (body.get("list") or []) if code == 200 else []:
        gid = str(rec.get("appConnectorGroupId"))
        for cid in ("connector_vpc_a", "connector_priv"):
            if gid == wanted[cid]["group_id"]:
                fill(cid, rec)
    return list(wanted.values())


def ec2_instances():
    raw = call("ec2", f"ec2.{REG}.amazonaws.com", REG, {
        "Action": "DescribeInstances", "Version": "2016-11-15",
        "Filter.1.Name": "tag:Project", "Filter.1.Value.1": PROJECT_TAG,
        "Filter.2.Name": "instance-state-name",
        "Filter.2.Value.1": "pending", "Filter.2.Value.2": "running",
        "Filter.2.Value.3": "stopping", "Filter.2.Value.4": "stopped"})
    if raw.startswith("HTTP"):
        return []
    root = ET.fromstring(raw)
    out = []
    for inst in root.iter(f"{NS}instancesSet"):
        for it in inst.findall(f"{NS}item"):
            name = next((t.findtext(f"{NS}value") for t in it.findall(f".//{NS}tagSet/{NS}item")
                         if t.findtext(f"{NS}key") == "Name"), None)
            out.append({"id": it.findtext(f"{NS}instanceId"), "name": name,
                        "state": it.findtext(f".//{NS}instanceState/{NS}name"),
                        "type": it.findtext(f"{NS}instanceType"),
                        "private_ip": it.findtext(f"{NS}privateIpAddress"),
                        "public_ip": it.findtext(f"{NS}ipAddress")})
    return sorted(out, key=lambda x: x["name"] or "")


def snapshot():
    comps = zpa_components()
    inst = ec2_instances()
    return {
        "healthy": all(c["authenticated"] for c in comps) and any(i["state"] == "running" for i in inst),
        "summary": f"{sum(c['authenticated'] for c in comps)}/{len(comps)} components authenticated, "
                   f"{sum(i['state'] == 'running' for i in inst)}/{len(inst)} instances running",
        "components": comps,
        "instances": inst,
        "region": REG,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit one JSON object")
    args = ap.parse_args()
    snap = snapshot()
    if args.json:
        print(json.dumps(snap))
        return
    print(f"=== EC2 ({REG}) ===")
    for i in snap["instances"]:
        print(f"  {i['name'] or '-':24} {i['id']}  {i['type']:10} {i['state']:9} {i['private_ip'] or ''}")
    print("\n=== ZPA enrolment ===")
    for c in snap["components"]:
        mark = "OK " if c["authenticated"] else "-- "
        print(f"  {mark}{c['label']:24} {c['control_channel'] or 'not enrolled':28} {c['version'] or ''}")
    print(f"\n{snap['summary']}")


if __name__ == "__main__":
    main()
