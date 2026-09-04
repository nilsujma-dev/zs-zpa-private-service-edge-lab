import re,sys,xml.etree.ElementTree as ET
sys.path.insert(0,"/private/tmp/claude-501/-Users-nilsujma-claude/6d2bff1d-b7c6-44ef-bda4-d9939a697344/scratchpad")
from aws_call import call
NS="{http://ec2.amazonaws.com/doc/2016-11-15/}"
def images(reg,pat):
    r=call("ec2",f"ec2.{reg}.amazonaws.com",reg,{"Action":"DescribeImages","Version":"2016-11-15",
        "Owner.1":"aws-marketplace","Filter.1.Name":"name","Filter.1.Value.1":pat})
    if r.startswith("HTTP"): return None
    root=ET.fromstring(r); out=[]
    for it in root.findall(f".//{NS}imagesSet/{NS}item"):
        g=lambda t:(it.findtext(f"{NS}{t}") or "")
        pcs=[(p.findtext(f"{NS}productCodeId"),p.findtext(f"{NS}productCodeType"))
             for p in it.findall(f".//{NS}productCodes/{NS}item")]
        out.append(dict(name=g("name"),id=g("imageId"),owner=g("imageOwnerId"),arch=g("architecture"),
            plat=g("platformDetails"),root=g("rootDeviceType"),virt=g("virtualizationType"),
            created=g("creationDate")[:10],state=g("imageState"),ena=g("enaSupport"),
            usage=g("usageOperation"),desc=g("description"),pcs=pcs))
    return out
for reg in ("eu-central-1","us-west-2","eu-west-1"):
    print(f"\n{'='*66}\n{reg}\n{'='*66}")
    got=images(reg,"zpa-*")
    if got is None: print("  API error"); continue
    for im in sorted(got,key=lambda x:x["name"]):
        print(f"\n  {im['name']}")
        print(f"    imageId {im['id']}   owner {im['owner']}   {im['arch']}/{im['virt']}/{im['root']}")
        print(f"    built {im['created']}  state={im['state']}  ena={im['ena']}  platform={im['plat']}")
        print(f"    usageOperation {im['usage']}")
        for pid,pty in im["pcs"]: print(f"    productCode {pid} ({pty})  <- marketplace subscription gate")
        if im["desc"]: print(f"    desc {im['desc'][:130]}")
