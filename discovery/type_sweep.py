import sys
sys.path.insert(0,"/private/tmp/claude-501/-Users-nilsujma-claude/6d2bff1d-b7c6-44ef-bda4-d9939a697344/scratchpad")
from aws_call import call
reg="eu-central-1"; host=f"ec2.{reg}.amazonaws.com"
AMIS=[("App Connector","ami-0aaa3d92aff1df4a0"),("Service Edge","ami-07811cc3852902146")]
TYPES=["t2.medium","t3.medium","t3.large","t3.xlarge","t3a.large",
       "m4.large","m4.xlarge","m5.large","m5.xlarge","m5.2xlarge","m5.4xlarge",
       "m5a.large","m5a.xlarge","m5n.large","m6i.large","m6i.xlarge","m6a.large","m7i.large",
       "c4.large","c5.large","c5.xlarge","c5.2xlarge","c5a.large","c5n.large","c6i.large","c6i.xlarge",
       "r5.large","r5.xlarge"]
res={}
for label,ami in AMIS:
    ok=[];bad=[];other=[]
    for t in TYPES:
        o=call("ec2",host,reg,{"Action":"RunInstances","Version":"2016-11-15","ImageId":ami,
            "InstanceType":t,"MinCount":"1","MaxCount":"1","DryRun":"true"})
        if "DryRunOperation" in o: ok.append(t)
        elif "not supported" in o: bad.append(t)
        elif "InvalidParameterValue" in o or "Unsupported" in o: other.append((t,"n/a in region"))
        else: other.append((t,o[:60]))
    res[label]=(ok,bad,other)
    print(f"\n### {label}  ({ami})")
    print(f"  SUPPORTED   ({len(ok)}): {', '.join(ok) if ok else '-'}")
    print(f"  BLOCKED     ({len(bad)}): {', '.join(bad) if bad else '-'}")
    if other: print(f"  other: {other}")
a=set(res['App Connector'][0]); b=set(res['Service Edge'][0])
print(f"\nIdentical allowlist across both images: {a==b}")
if a!=b:
    print("  AC only:",sorted(a-b)); print("  SE only:",sorted(b-a))
