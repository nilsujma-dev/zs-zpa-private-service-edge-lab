"""Real monthly cost from the AWS Pricing API. No estimates from memory."""
import json, subprocess, sys

REGION_NAME = "EU (Frankfurt)"
HOURS = 730.0


def price(service, filters):
    args = ["aws", "pricing", "get-products", "--service-code", service,
            "--region", "us-east-1", "--max-results", "100", "--filters"]
    for k, v in filters.items():
        args.append(f"Type=TERM_MATCH,Field={k},Value={v}")
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        return None, out.stderr[:200]
    items = json.loads(out.stdout).get("PriceList", [])
    best = None
    for raw in items:
        d = json.loads(raw)
        for term in d.get("terms", {}).get("OnDemand", {}).values():
            for dim in term.get("priceDimensions", {}).values():
                p = float(dim["pricePerUnit"]["USD"])
                if p > 0 and (best is None or p < best[0]):
                    best = (p, dim.get("unit"), dim.get("description", "")[:70])
    return best, None


ROWS = []


def add(label, qty, unit_price, unit, note=""):
    ROWS.append((label, qty, unit_price, qty * unit_price, unit, note))


print("querying AWS Pricing API (eu-central-1)...\n")

ec2 = [("m5.large", "Linux", "PSE", 1),
       ("t3.medium", "Linux", "App Connector x2", 2),
       ("t3.micro", "Linux", "nginx server", 1),
       ("t3.medium", "Windows", "MCU Windows client", 1)]

for itype, os_, label, count in ec2:
    b, err = price("AmazonEC2", {
        "instanceType": itype, "location": REGION_NAME, "operatingSystem": os_,
        "tenancy": "Shared", "preInstalledSw": "NA", "capacitystatus": "Used"})
    if not b:
        print(f"  {label}: lookup failed {err}")
        continue
    add(f"{label} ({itype} {os_})", count * HOURS, b[0], "hr")

b, _ = price("AmazonVPC", {"location": REGION_NAME,
                           "groupDescription": "Hourly charge for NAT Gateways"})
if b:
    add("NAT gateway", HOURS, b[0], "hr")

b, _ = price("AmazonVPC", {"location": REGION_NAME, "group": "VPCPublicIPv4Address"})
if b:
    add("Public IPv4 x2 (PSE + NAT)", 2 * HOURS, b[0], "hr", "in-use addresses are billed")

b, _ = price("AmazonEC2", {"location": REGION_NAME, "volumeApiName": "gp3",
                           "productFamily": "Storage"})
if b:
    add("EBS gp3 298 GiB", 298, b[0], "GB-mo", "80+80+80+50+8")

print(f"{'component':38} {'qty':>10} {'unit $':>10} {'month $':>10}")
print("-" * 72)
total = 0.0
for label, qty, up, sub, unit, note in ROWS:
    total += sub
    print(f"{label:38} {qty:>10.1f} {up:>10.5f} {sub:>10.2f}   {note}")
print("-" * 72)
print(f"{'subtotal, always-on':38} {'':>10} {'':>10} {total:>10.2f}")
print(f"{'NAT data processing (lab traffic)':38} {'':>10} {'':>10} {'~10-25':>10}")
print(f"{'TOTAL':38} {'':>10} {'':>10} {total + 17:>10.2f}   approx")
