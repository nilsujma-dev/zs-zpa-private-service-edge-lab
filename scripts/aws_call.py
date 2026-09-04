import os
# Generic read-only AWS SigV4 caller (stdlib only).
import datetime,hashlib,hmac,os,sys,urllib.request,urllib.error,urllib.parse
def call(service,host,region,params):
    AK=os.environ["AWS_ACCESS_KEY_ID"];SK=os.environ["AWS_SECRET_ACCESS_KEY"];ST=os.environ["AWS_SESSION_TOKEN"]
    payload=urllib.parse.urlencode(params)
    t=datetime.datetime.now(datetime.timezone.utc)
    ad=t.strftime("%Y%m%dT%H%M%SZ");ds=t.strftime("%Y%m%d")
    ph=hashlib.sha256(payload.encode()).hexdigest()
    ct="application/x-www-form-urlencoded; charset=utf-8";sh="content-type;host;x-amz-date;x-amz-security-token"
    canon=f"POST\n/\n\ncontent-type:{ct}\nhost:{host}\nx-amz-date:{ad}\nx-amz-security-token:{ST}\n\n{sh}\n{ph}"
    sc=f"{ds}/{region}/{service}/aws4_request"
    s2s=f"AWS4-HMAC-SHA256\n{ad}\n{sc}\n"+hashlib.sha256(canon.encode()).hexdigest()
    def _s(k,m):return hmac.new(k,m.encode(),hashlib.sha256).digest()
    k=_s(_s(_s(_s(("AWS4"+SK).encode(),ds),region),service),"aws4_request")
    sig=hmac.new(k,s2s.encode(),hashlib.sha256).hexdigest()
    req=urllib.request.Request(f"https://{host}/",data=payload.encode(),headers={
        "Content-Type":ct,"X-Amz-Date":ad,"X-Amz-Security-Token":ST,
        "Authorization":f"AWS4-HMAC-SHA256 Credential={AK}/{sc}, SignedHeaders={sh}, Signature={sig}"})
    try: return urllib.request.urlopen(req,timeout=25).read().decode()
    except urllib.error.HTTPError as e: return f"HTTP {e.code}: {e.read().decode()[:500]}"
if __name__=="__main__":
    import re
    reg=sys.argv[1] if len(sys.argv)>1 else "us-west-2"
    print(f"### REGION {reg} ###")
    r=call("ec2",f"ec2.{reg}.amazonaws.com",reg,{"Action":"DescribeVpcs","Version":"2016-11-15"})
    ids=re.findall(r"<vpcId>(.*?)</vpcId>",r); cidr=re.findall(r"<cidrBlock>(.*?)</cidrBlock>",r)
    dflt=re.findall(r"<isDefault>(.*?)</isDefault>",r)
    print("VPCs:",list(zip(ids,cidr,dflt)) if ids else r[:300])
    r2=call("ec2",f"ec2.{reg}.amazonaws.com",reg,{"Action":"DescribeInstances","Version":"2016-11-15"})
    print("Running instances:",len(re.findall(r"<instanceId>",r2)))
    r3=call("ec2",f"ec2.{reg}.amazonaws.com",reg,{"Action":"DescribeAvailabilityZones","Version":"2016-11-15"})
    print("AZs:",re.findall(r"<zoneName>(.*?)</zoneName>",r3))
