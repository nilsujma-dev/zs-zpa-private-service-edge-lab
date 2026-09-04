import re,sys,xml.etree.ElementTree as ET
sys.path.insert(0,"/private/tmp/claude-501/-Users-nilsujma-claude/6d2bff1d-b7c6-44ef-bda4-d9939a697344/scratchpad")
from aws_call import call
NS="{http://ec2.amazonaws.com/doc/2016-11-15/}"
reg="eu-central-1"; host=f"ec2.{reg}.amazonaws.com"
AMIS={"App Connector":"ami-0aaa3d92aff1df4a0","Private Service Edge":"ami-07811cc3852902146"}

print("--- AMI disk layout (hints at sizing) ---")
r=call("ec2",host,reg,{"Action":"DescribeImages","Version":"2016-11-15","Owner.1":"aws-marketplace",
    "Filter.1.Name":"name","Filter.1.Value.1":"zpa-*"})
root=ET.fromstring(r)
for it in root.findall(f".//{NS}imagesSet/{NS}item"):
    nm=it.findtext(f"{NS}name") or ""
    if not nm.startswith("zpa-"): continue
    short=nm.split("-el9")[0]
    for bd in it.findall(f".//{NS}blockDeviceMapping/{NS}item"):
        dn=bd.findtext(f"{NS}deviceName")
        sz=bd.findtext(f".//{NS}ebs/{NS}volumeSize"); vt=bd.findtext(f".//{NS}ebs/{NS}volumeType")
        enc=bd.findtext(f".//{NS}ebs/{NS}encrypted")
        if sz: print(f"  {short:24} {dn}  {sz} GiB {vt}  encrypted={enc}")

print("\n--- DryRun launch permission (creates nothing) ---")
for label,ami in AMIS.items():
    for itype in ("m5.large","m5.xlarge","c5.2xlarge"):
        out=call("ec2",host,reg,{"Action":"RunInstances","Version":"2016-11-15","ImageId":ami,
            "InstanceType":itype,"MinCount":"1","MaxCount":"1","DryRun":"true"})
        if "DryRunOperation" in out: verdict="LAUNCHABLE (dry-run passed)"
        elif "UnauthorizedOperation" in out: verdict="DENIED - IAM"
        elif "OptInRequired" in out or "subscript" in out.lower(): verdict="SUBSCRIPTION REQUIRED"
        else:
            m=re.search(r"<Code>(.*?)</Code>.*?<Message>(.*?)</Message>",out,re.S)
            verdict=f"{m.group(1)}: {m.group(2)[:90]}" if m else out[:110]
        print(f"  {label:22} {itype:12} -> {verdict}")

print("\n--- instance types offered in eu-central-1 ---")
o=call("ec2",host,reg,{"Action":"DescribeInstanceTypeOfferings","Version":"2016-11-15",
    "LocationType":"region","Filter.1.Name":"instance-type","Filter.1.Value.1":"m5.large",
    "Filter.1.Value.2":"m5.xlarge","Filter.1.Value.3":"c5.2xlarge","Filter.1.Value.4":"c5.large"})
print("  ",sorted(set(re.findall(r"<instanceType>(.*?)</instanceType>",o))))
