import sys,xml.etree.ElementTree as ET
sys.path.insert(0,"/private/tmp/claude-501/-Users-nilsujma-claude/6d2bff1d-b7c6-44ef-bda4-d9939a697344/scratchpad")
from aws_call import call
NS="{http://ec2.amazonaws.com/doc/2016-11-15/}"
for reg in ("eu-central-1","us-west-2","eu-west-1"):
    r=call("ec2",f"ec2.{reg}.amazonaws.com",reg,{"Action":"DescribeSecurityGroups","Version":"2016-11-15"})
    if r.startswith("HTTP"): print(reg,"ERR"); continue
    root=ET.fromstring(r)
    groups=[(g.findtext(f"{NS}groupName"),g.findtext(f"{NS}groupId"))
            for g in root.findall(f".//{NS}securityGroupInfo/{NS}item")]
    print(f"{reg}: {len(groups)} security group(s) -> {[n for n,_ in groups]}")
