"""Create the lab's ZPA objects: Service Edge Group, App Connector Group, provisioning keys.

Create-only and resume-safe. Existing AWS-Lab objects are reused by name and never
modified, with one guarded exception described below. Never issues DELETE. Never
touches an object it did not create.

Three API behaviours this script exists to get right (each cost a rebuild to find):

1. Enrollment certs are resolved by NAME ("Root", "Connector", "Service Edge") so the
   script is portable across tenants -- the ids differ per tenant.
2. Creating a Service Edge Group REQUIRES signingCertId, and when signingCertId and
   enrollmentCertId are both sent the API silently resolves the enrollment cert to the
   SIGNING cert. So the group is created in two calls: POST (lands on Root), then a
   PUT setting enrollmentCertId=Connector and isPublic=TRUE WITHOUT signingCertId.
   That is the pairing the tenant's own working group uses. The PUT is the one update
   this project performs and is guarded to this group's name.
3. Provisioning keys must NEVER be sent with signingCertId, and the cert is asserted
   after creation because the API does not error on the override.

The Service Edge key uses the Service Edge cert; the connector key uses Root, which
is the proven-working pairing in this tenant. Keys are resolved by FAMILY: the highest
`<family> v<N>` bound to the lab group is the current one (prune.py mints v<N+1> when
fewer than 5 uses remain and reseeds SSM); the v2 name below is only minted when no
member of the family exists. maxUsage is 200 (at the default of 5 the sixth rebuild
silently falls back to OAuth; prune.py raises older keys to 200 as well).
"""
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import prune_lib  # noqa: E402
from zpa_api import get, one, req, zpa_list  # noqa: E402

PREFIX = "AWS-Lab"
LAT, LON, LOC = "50.1109221", "8.6821267", "Frankfurt am Main, Germany"
SE_NAME = f"{PREFIX} PSE Group"
CG_NAME = f"{PREFIX} App Connector Group"
SE_KEY = f"{PREFIX} SERVICE_EDGE_GRP key v2"
CG_KEY = f"{PREFIX} CONNECTOR_GRP key v2"
MAX_USAGE = prune_lib.KEY_MAX_USAGE   # "200"
OUT = os.path.join(here, "zpa-created.json")


def certs():
    code, body = get("enrollmentCert?pagesize=100", "v2")
    if code != 200:
        sys.exit(f"enrollmentCert HTTP {code}")
    by_name = {i["name"]: str(i["id"]) for i in (body.get("list") or [])}
    missing = [n for n in ("Root", "Connector", "Service Edge") if n not in by_name]
    if missing:
        sys.exit(f"enrollment cert(s) not found in tenant: {missing}")
    return by_name


def index(resource):
    code, body = get(resource)
    return {i["name"]: i for i in (body.get("list") or [])} if code == 200 else {}


def ensure_group(resource, name, body):
    pool = index(resource)
    if name in pool:
        gid = str(pool[name]["id"])
        print(f"  {resource:20} REUSE  id={gid}  {name}")
        return gid
    code, rec = req("POST", resource, body)
    if code not in (200, 201):
        sys.exit(f"  {resource} HTTP {code}: {json.dumps(rec)[:300]}")
    print(f"  {resource:20} CREATE id={rec['id']}  {rec['name']}")
    return str(rec["id"])


def align_service_edge_group(gid, want_cert):
    """The one update: set enrollmentCertId=Connector and isPublic=TRUE, without signingCertId."""
    code, cur = get(f"serviceEdgeGroup/{gid}")
    if code != 200:
        sys.exit(f"fetch serviceEdgeGroup/{gid} HTTP {code}")
    if cur.get("name") != SE_NAME:
        sys.exit(f"REFUSING to update {gid}: name is {cur.get('name')!r}, not {SE_NAME!r}")
    if str(cur.get("enrollmentCertId")) == want_cert and str(cur.get("isPublic")).upper() == "TRUE":
        print(f"  serviceEdgeGroup     aligned (cert={want_cert}, isPublic=TRUE)")
        return
    drop = ("modifiedTime", "creationTime", "modifiedBy", "serviceEdges", "version",
            "versionProfileName", "cityCountry", "countryCode", "signingCertId")
    body = {k: v for k, v in cur.items() if k not in drop}
    body["enrollmentCertId"] = want_cert
    body["isPublic"] = "TRUE"
    code, rec = req("PUT", f"serviceEdgeGroup/{gid}", body)
    if code not in (200, 201, 204):
        sys.exit(f"  serviceEdgeGroup align PUT HTTP {code}: {json.dumps(rec)[:300]}")
    code, after = get(f"serviceEdgeGroup/{gid}")
    if str(after.get("enrollmentCertId")) != want_cert:
        sys.exit(f"  serviceEdgeGroup align FAILED: cert is {after.get('enrollmentCertId')}")
    print(f"  serviceEdgeGroup     ALIGNED cert={want_cert} isPublic={after.get('isPublic')}")


def ensure_key(assoc, name, cert, zcomponent_id):
    path = f"associationType/{assoc}/provisioningKey"
    keys = zpa_list(path)
    family, _ = prune_lib.key_family(name)
    rec = prune_lib.current_key(keys, family, zcomponent_id)
    if rec:
        one(keys, rec["name"], "provisioning keys")     # two of one name = stop
        print(f"  provisioningKey {assoc:17} REUSE  id={rec['id']} {rec.get('name')!r} "
              f"cert={rec.get('enrollmentCertId')} usage={rec.get('usageCount')}/{rec.get('maxUsage')}")
    else:
        code, rec = req("POST", path, {
            "name": name, "maxUsage": MAX_USAGE, "enrollmentCertId": cert,
            "zcomponentId": zcomponent_id, "enabled": True})   # deliberately no signingCertId
        if code not in (200, 201):
            sys.exit(f"  provisioningKey {assoc} HTTP {code}: {json.dumps(rec)[:300]}")
        rec = index(path).get(name) or rec
        print(f"  provisioningKey {assoc:17} CREATE id={rec['id']}")
    if str(rec.get("enrollmentCertId")) != cert:
        sys.exit(f"  provisioningKey {assoc}: cert is {rec.get('enrollmentCertId')}, expected {cert}")
    return str(rec["id"])


def main():
    c = certs()
    root, conn, se = c["Root"], c["Connector"], c["Service Edge"]
    print(f"enrollment certs: Root={root} Connector={conn} ServiceEdge={se}")
    made = json.load(open(OUT)) if os.path.exists(OUT) else {}

    se_id = ensure_group("serviceEdgeGroup", SE_NAME, {
        "name": SE_NAME,
        "description": "Standalone AWS lab PSE. Created by automation; belongs to no other deployment in this tenant.",
        "enabled": True, "latitude": LAT, "longitude": LON, "location": LOC, "isPublic": "TRUE",
        "upgradeDay": "SUNDAY", "upgradeTimeInSecs": "66600",
        "signingCertId": root, "enrollmentCertId": conn,
        "overrideVersionProfile": True, "versionProfileId": "0"})
    align_service_edge_group(se_id, conn)

    cg_id = ensure_group("appConnectorGroup", CG_NAME, {
        "name": CG_NAME,
        "description": "Standalone AWS lab App Connector, co-located with the lab PSE.",
        "enabled": True, "latitude": LAT, "longitude": LON, "location": LOC,
        "upgradeDay": "SUNDAY", "upgradeTimeInSecs": "66600", "dnsQueryType": "IPV4_IPV6",
        "signingCertId": root, "enrollmentCertId": conn,
        "overrideVersionProfile": True, "versionProfileId": "0"})

    made.update(
        serviceEdgeGroupId=se_id, appConnectorGroupId=cg_id,
        pseKeyId=ensure_key("SERVICE_EDGE_GRP", SE_KEY, se, se_id), pseKeyAssoc="SERVICE_EDGE_GRP",
        connKeyId=ensure_key("CONNECTOR_GRP", CG_KEY, root, cg_id), connKeyAssoc="CONNECTOR_GRP")
    json.dump(made, open(OUT, "w"), indent=2)
    print(json.dumps(made, indent=2))


if __name__ == "__main__":
    main()
