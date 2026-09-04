import os, datetime,hashlib,hmac,json,os,re,sys,urllib.request,urllib.error
sys.path.insert(0,"/private/tmp/claude-501/-Users-nilsujma-claude/6d2bff1d-b7c6-44ef-bda4-d9939a697344/scratchpad")
from aws_call import call
REG="eu-central-1"

def jcall(service,host,region,target,body):
    AK=os.environ["AWS_ACCESS_KEY_ID"];SK=os.environ["AWS_SECRET_ACCESS_KEY"];ST=os.environ["AWS_SESSION_TOKEN"]
    payload=json.dumps(body); t=datetime.datetime.now(datetime.timezone.utc)
    ad=t.strftime("%Y%m%dT%H%M%SZ"); ds=t.strftime("%Y%m%d")
    ph=hashlib.sha256(payload.encode()).hexdigest(); ct="application/x-amz-json-1.1"
    sh="content-type;host;x-amz-date;x-amz-security-token;x-amz-target"
    canon=(f"POST\n/\n\ncontent-type:{ct}\nhost:{host}\nx-amz-date:{ad}\n"
           f"x-amz-security-token:{ST}\nx-amz-target:{target}\n\n{sh}\n{ph}")
    sc=f"{ds}/{region}/{service}/aws4_request"
    s2s=f"AWS4-HMAC-SHA256\n{ad}\n{sc}\n"+hashlib.sha256(canon.encode()).hexdigest()
    def _s(k,m): return hmac.new(k,m.encode(),hashlib.sha256).digest()
    k=_s(_s(_s(_s(("AWS4"+SK).encode(),ds),region),service),"aws4_request")
    sig=hmac.new(k,s2s.encode(),hashlib.sha256).hexdigest()
    req=urllib.request.Request(f"https://{host}/",data=payload.encode(),headers={
        "Content-Type":ct,"X-Amz-Date":ad,"X-Amz-Security-Token":ST,"X-Amz-Target":target,
        "Authorization":f"AWS4-HMAC-SHA256 Credential={AK}/{sc}, SignedHeaders={sh}, Signature={sig}"})
    try: return json.load(urllib.request.urlopen(req,timeout=25))
    except urllib.error.HTTPError as e: return {"_err":e.code,"_body":e.read()[:200].decode("utf8","replace")}

QUOTAS=[("vpc","L-F678F1CE","VPCs per region"),
        ("ec2","L-0263D0A3","Elastic IPs per region"),
        ("ec2","L-1216C47A","Running on-demand standard vCPUs"),
        ("vpc","L-FE5A380F","NAT gateways per AZ"),
        ("vpc","L-45FE3B85","Egress-only internet gateways"),
        ("vpc","L-407747CB","Subnets per VPC")]
print("=== service quotas (eu-central-1) ===")
for svc,code,label in QUOTAS:
    r=jcall("servicequotas",f"servicequotas.{REG}.amazonaws.com",REG,
            "ServiceQuotasV20190624.GetServiceQuota",{"ServiceCode":svc,"QuotaCode":code})
    if "_err" in r: print(f"  {label:36} ERROR {r['_err']} {r['_body'][:90]}"); continue
    q=r.get("Quota",{})
    print(f"  {label:36} {q.get('Value')}  (adjustable={q.get('Adjustable')})")

print("\n=== current usage ===")
a=call("ec2",f"ec2.{REG}.amazonaws.com",REG,{"Action":"DescribeAddresses","Version":"2016-11-15"})
print("  Elastic IPs allocated:", 0 if a.startswith("HTTP") else len(re.findall(r"<publicIp>",a)))
v=call("ec2",f"ec2.{REG}.amazonaws.com",REG,{"Action":"DescribeVpcs","Version":"2016-11-15"})
print("  VPCs existing:", len(re.findall(r"<vpcId>",v)), "(default only)")
n=call("ec2",f"ec2.{REG}.amazonaws.com",REG,{"Action":"DescribeNatGateways","Version":"2016-11-15"})
print("  NAT gateways:", 0 if n.startswith("HTTP") else len(re.findall(r"<natGatewayId>",n)))
