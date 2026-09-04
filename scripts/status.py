"""Read-only status: EC2 health + ZPA enrolment for the AWS-Lab groups."""
import json, os, re, sys, xml.etree.ElementTree as ET

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from aws_call import call
from zpa_api import get

NS = "{http://ec2.amazonaws.com/doc/2016-11-15/}"
REG = "eu-central-1"
made = json.load(open(os.path.join(here, "zpa-created.json")))

print("=== EC2 ===")
r = call("ec2", f"ec2.{REG}.amazonaws.com", REG,
         {"Action": "DescribeInstanceStatus", "Version": "2016-11-15",
          "IncludeAllInstances": "true"})
if r.startswith("HTTP"):
    print("  ", r[:160])
else:
    root = ET.fromstring(r)
    for it in root.findall(f".//{NS}instanceStatusSet/{NS}item"):
        iid = it.findtext(f"{NS}instanceId")
        state = it.findtext(f".//{NS}instanceState/{NS}name")
        sys_ok = it.findtext(f".//{NS}systemStatus/{NS}status")
        ins_ok = it.findtext(f".//{NS}instanceStatus/{NS}status")
        print(f"  {iid}  state={state}  system={sys_ok}  instance={ins_ok}")

print("\n=== ZPA enrolment ===")
code, j = get("serviceEdge")
if code == 200:
    rows = [s for s in (j.get("list") or [])
            if str(s.get("serviceEdgeGroupId")) == made["serviceEdgeGroupId"]]
    print(f"  Service Edges in AWS-Lab PSE Group: {len(rows)}")
    for s in rows:
        print(f"    {s.get('name')}  ctrlChannel={s.get('controlChannelStatus')} "
              f"upgrade={s.get('upgradeStatus')} enrolled={s.get('enrollmentCert', {}).get('name', '-')}")
else:
    print(f"  serviceEdge: HTTP {code}")

code, j = get("connector")
if code == 200:
    rows = [c for c in (j.get("list") or [])
            if str(c.get("appConnectorGroupId")) == made["appConnectorGroupId"]]
    print(f"  App Connectors in AWS-Lab group: {len(rows)}")
    for c in rows:
        print(f"    {c.get('name')}  ctrlChannel={c.get('controlChannelStatus')} "
              f"version={c.get('applicationStartTime') and c.get('currentVersion')}")
else:
    print(f"  connector: HTTP {code}")
