import re,sys
sys.path.insert(0,"/private/tmp/claude-501/-Users-nilsujma-claude/6d2bff1d-b7c6-44ef-bda4-d9939a697344/scratchpad")
from aws_call import call
reg=sys.argv[1]
def probe(pat):
    p={"Action":"DescribeImages","Version":"2016-11-15","Owner.1":"aws-marketplace",
       "Filter.1.Name":"name","Filter.1.Value.1":pat}
    r=call("ec2",f"ec2.{reg}.amazonaws.com",reg,p)
    if r.startswith("HTTP"): return None,r[:160]
    return re.findall(r"<name>(.*?)</name>",r),None
print(f"===== {reg} :: Owner=aws-marketplace (param form) =====")
print("--- CALIBRATION ---")
for pat in ("*fortigate*","*FortiGate*","*Catalyst*","*paloalto*","*checkpoint*","*Check Point*"):
    n,e=probe(pat); print(f"  {pat:16} -> {'ERR '+e if e else str(len(n))+' hit(s)'}"+(f"   e.g. {n[0][:62]}" if n else ""))
print("--- TARGET ---")
for pat in ("*zscaler*","*Zscaler*","*zpa*","*ZPA*","*private-access*","*Private Access*",
            "*service-edge*","*Service Edge*","*app-connector*","*App Connector*",
            "*Access Connector*","*Access Service Edge*"):
    n,e=probe(pat)
    print(f"  {pat:22} -> {'ERR '+e if e else str(len(n))+' hit(s)'}")
    if n:
        for x in sorted(set(n))[:8]: print(f"        {x[:92]}")
