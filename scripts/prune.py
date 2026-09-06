"""Prune stale lab-owned ZPA entries (Service Edges and App Connectors) and keep the lab's
provisioning keys healthy. Dry-run by default; nothing is written without --apply.

    python3 scripts/prune.py                       dry-run (what ./lab.sh prune runs)
    python3 scripts/prune.py --apply               write, with the safety checks below
    python3 scripts/prune.py --phase off|on-pre|all
    python3 scripts/prune.py --only service_edge --first 1 --apply --max 1   one supervised deletion
    python3 scripts/prune.py --selftest            offline: synthetic records incl. production look-alikes
    python3 scripts/prune.py --json                one JSON object on stdout at the end

Candidates are ONLY entries inside lab-owned groups, resolved from exact group names (the
ownership rule lives in prune_lib.py, byte-identical in the Cloud Connector lab):

  connector     appConnectorGroupId in {id("AWS-Lab App Connector Group"), id("AWS-Lab PRIV Connector Group")}
  service edge  serviceEdgeGroupId == id("AWS-Lab PSE Group")
  key           name starts with AWS-Lab AND zcomponentId is one of those three groups

Stale = lab-owned AND not ZPN_STATUS_AUTHENTICATED AND older than --min-age-min (30) AND no
broker activity for 15 min AND no EC2 instance (running or stopped, Project=zpa-pse-lab) holds
its private ip -- a `lab.sh stop`ped instance keeps its entry. Without AWS credentials the run
refuses unless --no-aws-check is given (then the instance clause is recorded as "unchecked").

Order: connectors -> service edges -> keys (PUT maxUsage 200 on each current key; mint
<family> v<N+1> on the same group with the same cert when fewer than 5 uses remain, reseed
SSM, update zpa-created.json; delete retired keys once nothing is enrolled with them) ->
guard re-read of everything outside the lab. ZPA needs no activation.

Exit codes: 0 done (API failures are tolerated: the entry stays counted under `stale`);
2 usage / no AWS data; 3 ZCC_READ_ONLY lock; 7 candidates exceed --max (default 12 = three
components x four sets); 8 ownership, never-touch anchor or ambiguity; 9 the guard snapshot
of everything outside the lab differs after the run, or a key read-back differs.
"""
import argparse
import datetime
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prune_lib as PL  # noqa: E402
import put_keys_ssm  # noqa: E402
import zpa_api  # noqa: E402
from aws_call import call, have_creds  # noqa: E402
from zpa_api import redact, req, zpa_list  # noqa: E402

NS = "{http://ec2.amazonaws.com/doc/2016-11-15/}"
REG = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
EC2 = f"ec2.{REG}.amazonaws.com"
PROJECT_TAG = "zpa-pse-lab"
PREFIX = "AWS-Lab"
CG_NAMES = ("AWS-Lab App Connector Group", "AWS-Lab PRIV Connector Group")
SE_GROUP = "AWS-Lab PSE Group"
CERT_FOR = {"CONNECTOR_GRP": "Root", "SERVICE_EDGE_GRP": "Service Edge"}   # docs/runbook.md 3+4: never signingCertId
STATE = os.path.join(HERE, "prune-last.json")          # git-ignored; status.py reads it
DEFAULT_MAX = 12
TYPES = ("connector", "service_edge", "keys")
PHASES = {"off": TYPES, "on-pre": TYPES, "all": TYPES}
OUT = {"phase": None, "dry_run": True, "candidates": [], "kept": [], "skipped": {}, "failed": [],
       "pruned": {"connectors": 0, "service_edges": 0, "cc_vms": 0, "cc_groups": 0, "locations": 0,
                  "keys_rotated": 0, "keys_deleted": 0}, "keys": []}


def log(line):
    print(line)


def fail(code, msg):
    print(f"REFUSING ({code}): {msg}", file=sys.stderr)
    OUT["refused"] = {"code": code, "msg": msg}
    sys.exit(code)


def facts_line(kind, f, keys=("id", "group", "name", "status", "created", "disconnected", "key", "instance")):
    parts = [kind]
    for k in keys:
        if k in f and f[k] is not None:
            v = f[k]
            parts.append(f'{k}="{v}"' if isinstance(v, str) and " " in v else f"{k}={v}")
    return " ".join(parts)


# --------------------------------------------------------------------------- AWS

def ec2_instances():
    """Non-terminated instances tagged Project=zpa-pse-lab, with every private ip of every ENI."""
    raw = call("ec2", EC2, REG, {
        "Action": "DescribeInstances", "Version": "2016-11-15",
        "Filter.1.Name": "tag:Project", "Filter.1.Value.1": PROJECT_TAG,
        "Filter.2.Name": "instance-state-name", "Filter.2.Value.1": "pending", "Filter.2.Value.2": "running",
        "Filter.2.Value.3": "stopping", "Filter.2.Value.4": "stopped", "Filter.2.Value.5": "shutting-down"})
    if raw.startswith("HTTP"):
        fail(2, f"EC2 DescribeInstances failed: {raw[:160]} -- cannot prove which entries still have an instance")
    out = []
    for it in ET.fromstring(raw).iter(f"{NS}instancesSet"):
        for inst in it.findall(f"{NS}item"):
            ips = [x.text for x in inst.iter(f"{NS}privateIpAddress")]
            out.append({"id": inst.findtext(f"{NS}instanceId"), "state": inst.findtext(f".//{NS}instanceState/{NS}name"),
                        "private_ip": inst.findtext(f"{NS}privateIpAddress"), "private_ips": sorted({i for i in ips if i})})
    return out


# --------------------------------------------------------------------------- tenant

def read_zpa():
    Z = {"acgs": zpa_list("appConnectorGroup"), "segs": zpa_list("serviceEdgeGroup"),
         "connectors": zpa_list("connector"), "edges": zpa_list("serviceEdge"),
         "keys": {a: zpa_list(f"associationType/{a}/provisioningKey") for a in ("CONNECTOR_GRP", "SERVICE_EDGE_GRP")}}
    Z["certs"] = {str(c["id"]): c["name"] for c in zpa_list("enrollmentCert", ver="v2", pagesize=100)}
    return Z


def snapshot(Z, groups):
    return PL.guard_snapshot(Z["connectors"], Z["edges"], Z["acgs"], Z["segs"], groups["cg"], groups["se"], keys_by_assoc=Z["keys"])


def delete(path, what):
    if zpa_api.DRY_RUN:
        return "would"
    code, body = req("DELETE", path)
    if code in (200, 202, 204):
        return "deleted"
    if code == 404:
        return "gone"
    OUT["failed"].append({"what": what, "http": code, "body": json.dumps(redact(body))[:200]})
    return f"failed HTTP {code}"


def verdict_str(res):
    return {"would": "WOULD DELETE", "deleted": "DELETE ok", "gone": "DELETE 404 (already gone)"}.get(res, res.upper())


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write; without it the run is a dry-run")
    ap.add_argument("--dry-run", action="store_true", help="(default) read, print WOULD DELETE lines, write nothing")
    ap.add_argument("--phase", choices=sorted(PHASES), default="all")
    ap.add_argument("--only", action="append", choices=TYPES, help="restrict to one object type (repeatable)")
    ap.add_argument("--first", type=int, default=0, help="process only the first N candidates (oldest first); the rest are DEFERred")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX, help=f"refuse (exit 7) when more than N entries would be deleted (default {DEFAULT_MAX})")
    ap.add_argument("--min-age-min", type=int, default=PL.DEFAULT_MIN_AGE_MIN)
    ap.add_argument("--no-aws-check", action="store_true", help="allow a run without AWS credentials (instance clause unchecked)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        PL.selftest()
        return
    if args.apply and args.dry_run:
        fail(2, "--apply and --dry-run together")
    zpa_api.DRY_RUN = not args.apply
    OUT.update(phase=args.phase, dry_run=zpa_api.DRY_RUN)
    steps = tuple(t for t in PHASES[args.phase] if not args.only or t in args.only)
    now = time.time()
    if zpa_api.DRY_RUN:
        print("DRY RUN: no writes will be made", file=sys.stderr)
    if zpa_api.READ_ONLY_LOCK and args.apply:
        fail(3, "ZCC_READ_ONLY is set; --apply refused")

    aws = have_creds() and not args.no_aws_check
    if not have_creds() and not args.no_aws_check:
        fail(2, "no AWS credentials: cannot prove an entry has no instance. Pass --no-aws-check to run on tenant data alone.")
    instances = ec2_instances() if aws else None
    log(f"aws: {'checked ' + str(len(instances)) + ' non-terminated instance(s) tagged Project=' + PROJECT_TAG if aws else 'NOT CHECKED (--no-aws-check)'}")

    Z = read_zpa()
    try:
        groups = PL.resolve_lab_groups(Z["acgs"], Z["segs"], CG_NAMES, SE_GROUP)
    except PL.Refuse as e:
        fail(e.code, str(e))
    present = {"zpa.serviceEdgeGroup": {str(g["id"]): g.get("name") for g in Z["segs"]},
               "zpa.appConnectorGroup": {str(g["id"]): g.get("name") for g in Z["acgs"]}}
    problems = PL.check_anchors(present, ("zpa",))
    if problems:
        fail(PL.EXIT_OWNERSHIP, "; ".join(problems))
    log(f"lab groups: connectors {groups['cg'] or 'none yet'}; service edges {groups['se'] or 'none yet'}; "
        f"never-touch ZPA anchors present: {sum(len(PL.NEVER_TOUCH[k]) for k in PL.ANCHOR_KINDS['zpa'])}")
    before = snapshot(Z, groups)

    cands = {t: [] for t in TYPES}
    entries_by_key = {}
    for kind, t, recs, lab_ids in (("connector", "connector", Z["connectors"], groups["cg"]),
                                   ("serviceEdge", "service_edge", Z["edges"], groups["se"])):
        for c in recs:
            entries_by_key.setdefault(str(c.get("provisioningKeyId")), []).append(str(c.get("id")))
            v, why, f = PL.classify_zpa_entry(c, kind, lab_ids, now, instances, args.min_age_min, aws_checked=aws)
            if v == "SKIP":
                OUT["skipped"][t] = OUT["skipped"].get(t, 0) + 1
                continue
            if v == "PRUNE" and t in steps:
                cands[t].append((PL.epoch(c.get("creationTime")) or 0, f, c))
            else:
                OUT["kept"].append({"kind": f"zpa.{kind}", **f, "reason": why})
                log(f"KEEP  {facts_line('zpa.' + kind, f)} reason={why}")

    ordered = []
    for t in ("connector", "service_edge"):
        cands[t].sort(key=lambda x: x[0])
        ordered += [(t,) + c for c in cands[t]]
    deferred = ordered[args.first:] if args.first else []
    ordered = ordered[:args.first] if args.first else ordered
    for t, _, f, _ in ordered:
        try:
            PL.assert_not_never_touch("zpa.appConnectorGroup" if t == "connector" else "zpa.serviceEdgeGroup", f["group_id"], f["group"])
        except PL.Refuse as e:
            fail(e.code, str(e))
        OUT["candidates"].append({"kind": "zpa.connector" if t == "connector" else "zpa.serviceEdge", **f})
    for t, _, f, _ in deferred:
        log(f"DEFER {facts_line(t, f)} (beyond --first {args.first})")
    if len(ordered) > args.max:
        for t, _, f, _ in ordered:
            log(f"CAND  {facts_line(t, f)}")
        fail(PL.EXIT_CAP, f"{len(ordered)} candidates exceed --max {args.max}; nothing written. Use --first N / --only TYPE to prune in batches.")

    lab_ids = {**groups["cg"], **groups["se"]}
    mine = PL.lab_keys(Z["keys"]["CONNECTOR_GRP"], "CONNECTOR_GRP", groups["cg"], PREFIX) + \
        PL.lab_keys(Z["keys"]["SERVICE_EDGE_GRP"], "SERVICE_EDGE_GRP", groups["se"], PREFIX)
    will_delete = [f["id"] for _, _, f, _ in ordered]
    kplan = PL.key_plan(mine, entries_by_key, deleted_entry_ids=will_delete) if "keys" in steps else []
    for k in mine:
        b, _ = PL.key_family(k.get("name"))
        OUT["keys"].append({"type": k["_assoc"], "name": k.get("name"), "usage": k.get("usageCount"), "max": k.get("maxUsage"),
                            "current": PL.current_key([x for x in mine if x["_assoc"] == k["_assoc"]], b, k.get("zcomponentId")) is k})

    for t, _, f, rec in ordered:
        res = delete(f"{'connector' if t == 'connector' else 'serviceEdge'}/{f['id']}", f"{t} {f['id']}")
        log(f"PRUNE {facts_line('zpa.connector' if t == 'connector' else 'zpa.serviceEdge', f)} -> {verdict_str(res)}")
        if res in ("deleted", "gone", "would"):
            OUT["pruned"]["connectors" if t == "connector" else "service_edges"] += 1

    for a in kplan:
        k = a["key"]
        path = f"associationType/{k['_assoc']}/provisioningKey"
        base = f"KEY   zpa.key/{k['_assoc']} id={k.get('id')} name=\"{k.get('name')}\" usage={k.get('usageCount')}/{k.get('maxUsage')} group={k.get('zcomponentName')!r}"
        if a["op"] == "keep":
            log(f"{base} -> KEEP retired ({a['reason']})")
        elif a["op"] == "max_usage":
            log(f"{base} -> {'WOULD ' if zpa_api.DRY_RUN else ''}PUT maxUsage={a['max_usage']}")
            if not zpa_api.DRY_RUN:
                key_set_max_usage(k, path, a["max_usage"])
        elif a["op"] == "rotate":
            fam, _ = PL.key_family(k.get("name"))
            p = put_keys_ssm.param_for(k["_assoc"], fam)
            log(f"{base} -> {'WOULD ' if zpa_api.DRY_RUN else ''}MINT {a['new_name']!r} on the same group, cert {CERT_FOR[k['_assoc']]}; "
                f"reseed SSM {p[5] if p else '(no parameter for this family!)'}")
            if not zpa_api.DRY_RUN:
                key_rotate(k, path, a["new_name"], Z["certs"], p)
            OUT["pruned"]["keys_rotated"] += 1
        elif a["op"] == "delete":
            res = delete(f"{path}/{k['id']}", f"key {k['id']}")
            log(f"{base} -> retired, {verdict_str(res)}")
            if res in ("deleted", "gone", "would"):
                OUT["pruned"]["keys_deleted"] += 1

    if args.apply:
        after = snapshot(read_zpa(), groups)
        diff = PL.guard_diff(before, after)
        if diff:
            for d in diff:
                log(f"GUARD {d}")
            fail(PL.EXIT_VERIFY, f"{len(diff)} object(s) outside the lab changed during the run")
        log("guard: everything outside the lab is unchanged")
        with open(STATE, "w") as fh:
            json.dump({"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "phase": args.phase,
                       "deleted": OUT["pruned"]["connectors"] + OUT["pruned"]["service_edges"], "pruned": OUT["pruned"]}, fh)
    log(f"pruned: {json.dumps(OUT['pruned'])}" + ("  (dry-run: nothing written)" if zpa_api.DRY_RUN else "") +
        (f"  failed: {len(OUT['failed'])}" if OUT["failed"] else "") + (f"  deferred: {len(deferred)}" if deferred else ""))
    if args.json:
        print(json.dumps(OUT, sort_keys=True))


# --------------------------------------------------------------------------- key writes

KEY_DROP = ("creationTime", "modifiedTime", "modifiedBy", "provisioningKey", "signingCertId", "enrollmentCertName",
            "zcomponentName", "usageCount", "readOnly", "zscalerManaged", "microtenantName", "microtenantId", "_assoc")


def key_read(path, kid):
    return next((k for k in zpa_list(path) if str(k.get("id")) == str(kid)), None)


def key_set_max_usage(k, path, max_usage):
    """GET -> drop the secret, signingCertId and timestamps -> set maxUsage -> PUT -> GET -> assert
    (the flow align_service_edge_group in zpa_create.py already uses on this tenant)."""
    body = {x: v for x, v in k.items() if x not in KEY_DROP}
    body["maxUsage"] = str(max_usage)
    code, rec = req("PUT", f"{path}/{k['id']}", body)
    if code not in (200, 201, 204):
        OUT["failed"].append({"what": f"key {k['id']} PUT maxUsage", "http": code})
        log(f"      PUT HTTP {code}: {json.dumps(redact(rec))[:200]}")
        return
    after = key_read(path, k["id"]) or {}
    for fld in ("enrollmentCertId", "zcomponentId", "enabled"):
        if str(after.get(fld)) != str(k.get(fld)):
            fail(PL.EXIT_VERIFY, f"key {k['id']}: {fld} changed by the PUT ({k.get(fld)} -> {after.get(fld)}); stop and inspect")
    if str(after.get("maxUsage")) != str(max_usage):
        fail(PL.EXIT_VERIFY, f"key {k['id']}: maxUsage read back as {after.get('maxUsage')}, expected {max_usage}")
    log(f"      maxUsage={after.get('maxUsage')} cert={after.get('enrollmentCertName')} group={after.get('zcomponentName')!r} verified")


def key_rotate(k, path, new_name, certs, param):
    """Mint <family> v<N+1> on the same group with the cert the runbook prescribes (Root for
    connector keys, Service Edge for the PSE key), never sending signingCertId; assert the cert
    landed; reseed SSM with the NEW value; point zpa-created.json at it."""
    cert = str(k.get("enrollmentCertId"))
    want = CERT_FOR[k["_assoc"]]
    if certs.get(cert) != want:
        fail(PL.EXIT_VERIFY, f"key {k['id']} enrollment cert {cert} is {certs.get(cert)!r}, expected {want!r}; refusing to mint a copy")
    if any(str(x.get("name")) == new_name for x in zpa_list(path)):
        log(f"      {new_name!r} already exists; not minting again")
        return
    body = {"name": new_name, "maxUsage": PL.KEY_MAX_USAGE, "enrollmentCertId": cert,
            "zcomponentId": str(k["zcomponentId"]), "enabled": True}      # deliberately no signingCertId
    code, rec = req("POST", path, body)
    if code not in (200, 201):
        OUT["failed"].append({"what": f"mint {new_name}", "http": code})
        log(f"      POST HTTP {code}: {json.dumps(redact(rec))[:200]}")
        return
    new = next((x for x in zpa_list(path) if str(x.get("name")) == new_name), None) or rec
    if str(new.get("enrollmentCertId")) != cert or str(new.get("zcomponentId")) != str(k["zcomponentId"]):
        fail(PL.EXIT_VERIFY, f"minted key {new.get('id')} read back with cert {new.get('enrollmentCertId')} group {new.get('zcomponentId')}; expected {cert}/{k['zcomponentId']}")
    log(f"      minted id={new.get('id')} cert={new.get('enrollmentCertName')} usage={new.get('usageCount')}/{new.get('maxUsage')}")
    if not param:
        OUT["failed"].append({"what": f"no SSM parameter mapped for {new_name}"})
        return
    sc, sj = put_keys_ssm.seed(param[5], new)
    log(f"      SSM {param[5]} HTTP {sc} version={sj.get('Version')}")
    if sc != 200:
        OUT["failed"].append({"what": f"SSM {param[5]}", "http": sc})
        return
    put_keys_ssm.record_created(param[1], new["id"])


if __name__ == "__main__":
    main()
