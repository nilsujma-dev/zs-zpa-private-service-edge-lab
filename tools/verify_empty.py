import re,sys
sys.path.insert(0,"/private/tmp/claude-501/-Users-nilsujma-claude/6d2bff1d-b7c6-44ef-bda4-d9939a697344/scratchpad")
from aws_call import call
for reg in ("eu-central-1","us-west-2","eu-west-1"):
    h=f"ec2.{reg}.amazonaws.com"
    def n(action,tag,extra=None):
        p={"Action":action,"Version":"2016-11-15"}
        if extra: p.update(extra)
        r=call("ec2",h,reg,p)
        return "ERR" if r.startswith("HTTP") else len(re.findall(tag,r))
    vpcs=call("ec2",h,reg,{"Action":"DescribeVpcs","Version":"2016-11-15"})
    nondefault = 0 if vpcs.startswith("HTTP") else vpcs.count("<isDefault>false</isDefault>")
    print(f"{reg}:")
    print(f"   VPCs total {n('DescribeVpcs','<vpcId>')}  (non-default: {nondefault})")
    print(f"   EC2 instances    {n('DescribeInstances','<instanceId>')}")
    print(f"   NAT gateways     {n('DescribeNatGateways','<natGatewayId>')}")
    print(f"   Load balancers   n/a (elbv2)")
    print(f"   Non-default SGs  {n('DescribeSecurityGroups','<groupId>')-1 if isinstance(n('DescribeSecurityGroups','<groupId>'),int) else 'ERR'} beyond default")
