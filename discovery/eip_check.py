import re,sys,xml.etree.ElementTree as ET
sys.path.insert(0,"/private/tmp/claude-501/-Users-nilsujma-claude/6d2bff1d-b7c6-44ef-bda4-d9939a697344/scratchpad")
from aws_call import call
NS="{http://ec2.amazonaws.com/doc/2016-11-15/}"
for reg in ("eu-central-1","us-west-2","eu-west-1"):
    r=call("ec2",f"ec2.{reg}.amazonaws.com",reg,{"Action":"DescribeAddresses","Version":"2016-11-15"})
    if r.startswith("HTTP"): print(reg,"ERR",r[:100]); continue
    root=ET.fromstring(r); items=root.findall(f".//{NS}addressesSet/{NS}item")
    print(f"{reg}: {len(items)} elastic IP(s)")
    for it in items:
        g=lambda t: it.findtext(f"{NS}{t}") or "-"
        assoc = g("associationId")
        print(f"   {g('publicIp'):16} alloc={g('allocationId')} assoc={assoc} "
              f"instance={g('instanceId')} nif={g('networkInterfaceId')} domain={g('domain')}")
        print(f"   {'':16} -> {'UNATTACHED (billed hourly)' if assoc=='-' else 'in use'}")
