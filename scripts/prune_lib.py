"""Ownership and staleness rules for pruning lab-owned entries from the shared Zscaler tenant.

THIS FILE IS COPIED VERBATIM INTO TWO REPOSITORIES AND MUST STAY BYTE-IDENTICAL:

    zs-zpa-private-service-edge-lab/scripts/prune_lib.py
    zs-zcc-aws-workload-lab/scripts/prune_lib.py

Edit one, copy it over the other, and check `shasum` agrees. Stdlib only, no I/O, no tenant
or AWS calls: every function is a pure decision over records the caller has already read, so
the rule is testable offline (`selftest()`) and provably the same in both labs.

The rule, in one sentence: an entry is ours only if the tenant itself says so through GROUP
MEMBERSHIP resolved from an exact lab group name; a name prefix is a second, independent
condition, never the only one; status alone decides nothing. See docs/runbook.md,
"Pruning stale entries", and the design note the two runbooks cite.

Exit codes the callers use (the spec's numbers):
    7  candidates exceed --max
    8  ownership / never-touch / anchor / ambiguity / foreign pending edits
    9  the guard snapshot differs after the run (something outside the lab moved)
"""
import re

EXIT_CAP = 7
EXIT_OWNERSHIP = 8
EXIT_VERIFY = 9

AUTH = "ZPN_STATUS_AUTHENTICATED"
DEFAULT_MIN_AGE_MIN = 30          # entry younger than this is never a candidate
DEFAULT_DISCONNECT_MIN = 15       # no broker activity for at least this long
KEY_ROTATE_HEADROOM = 5           # mint v<N+1> when maxUsage - usageCount < this
KEY_MAX_USAGE = "200"

# --------------------------------------------------------------------------- never-touch
# Production anchors of this tenant. Asserted (id AND exact name present) before any write:
# a missing anchor means the resolver is looking at a different tenant than the one this
# rule was written for, and the run refuses. A candidate whose parent is in this table is a
# refusal too. These are opaque object ids, not credentials.
NEVER_TOUCH = {
    "zpa.serviceEdgeGroup": {"72063920524755031": "ZPA PSE Group"},
    "zpa.appConnectorGroup": {
        "72063920524754973": "OT-EBC AppConnector Group",
        "72063920524755048": "OT App Connector Group",
        "72063920524755098": "Remote-Sites Brussels",
        "72063920524755100": "Remote-Sites Frankfurt",
        "72063920524755106": "Remote Site Zaandam",
    },
    "ztw.ecgroup": {
        "196246245": "Branch1-Zaandam",
        "195093841": "Branch2-Frankfurt",
        "195093657": "Branch3-Brussels",
        "195784662": "HQ-OT-PERDUE35",
        "195784847": "IOT-OT-EBC-PERDUE-5",
    },
    "ztw.apiKeys": {"9672": "Default_Name"},
    "ztw.ecRules/ecRdr": {"1968870": "Default Forwarding Rule"},
    "zia.locations": {"195126546": "ORG_DEFAULT"},
}
# Planes each lab reads; anchors of planes a lab never reads are not asserted (the PSE lab
# has no ZTW/ZIA entitlement in its flow).
ANCHOR_KINDS = {
    "zpa": ("zpa.serviceEdgeGroup", "zpa.appConnectorGroup"),
    "ztw": ("ztw.ecgroup", "ztw.apiKeys", "ztw.ecRules/ecRdr"),
    "zia": ("zia.locations",),
}


class Refuse(Exception):
    """A safety violation: carries the exit code the caller must use."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------- small helpers

def epoch(v):
    """ZPA/ZTW timestamps are epoch seconds as strings (ms if implausibly large)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    return f / 1000.0 if f > 1e11 else f


def fmt_age(seconds):
    if seconds is None:
        return "?"
    s = int(max(seconds, 0))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def sid(v):
    return None if v is None else str(v)


_KEY_VER = re.compile(r"^(?P<base>.*?)\s+v(?P<ver>\d+)$")


def key_family(name):
    """'AWS-Lab X key v2' -> ('AWS-Lab X key', 2); 'AWS-Lab X key' -> ('AWS-Lab X key', 1)."""
    name = str(name or "").strip()
    m = _KEY_VER.match(name)
    if m:
        return m.group("base"), int(m.group("ver"))
    return name, 1


def key_name(base, version):
    return f"{base} v{version}"


# --------------------------------------------------------------------------- lab groups

def resolve_lab_groups(acgs, segs, cg_names, se_group_name=None):
    """{'cg': {id: name}, 'se': {id: name}} for the exact lab group names. Two groups of one
    name = Refuse(8): somebody created something by hand and the automation must not guess.
    A lab group that does not exist yet is simply absent (nothing can be inside it)."""
    out = {"cg": {}, "se": {}}
    for want in cg_names:
        hits = [g for g in acgs if str(g.get("name")) == want]
        if len(hits) > 1:
            raise Refuse(EXIT_OWNERSHIP, f"AMBIGUOUS: {len(hits)} appConnectorGroup named {want!r}: {[h.get('id') for h in hits]}")
        if hits:
            out["cg"][sid(hits[0]["id"])] = want
    if se_group_name:
        hits = [g for g in segs if str(g.get("name")) == se_group_name]
        if len(hits) > 1:
            raise Refuse(EXIT_OWNERSHIP, f"AMBIGUOUS: {len(hits)} serviceEdgeGroup named {se_group_name!r}: {[h.get('id') for h in hits]}")
        if hits:
            out["se"][sid(hits[0]["id"])] = se_group_name
    return out


def check_anchors(present, planes):
    """present: {kind: {id: name}} as read from the tenant. Returns the list of problems
    (empty = every anchor of every requested plane is there with its exact name)."""
    problems = []
    for plane in planes:
        for kind in ANCHOR_KINDS.get(plane, ()):
            have = present.get(kind) or {}
            for aid, aname in NEVER_TOUCH[kind].items():
                if aid not in have:
                    problems.append(f"anchor missing: {kind} id={aid} ({aname!r})")
                elif str(have[aid]) != aname:
                    problems.append(f"anchor renamed: {kind} id={aid} is {have[aid]!r}, expected {aname!r}")
    return problems


def assert_not_never_touch(kind, parent_id, parent_name=None):
    ids = NEVER_TOUCH.get(kind) or {}
    if sid(parent_id) in ids:
        raise Refuse(EXIT_OWNERSHIP, f"never-touch: {kind} id={parent_id} ({ids[sid(parent_id)]!r}) is among the candidates' parents")
    if kind in ("zpa.appConnectorGroup", "zpa.serviceEdgeGroup") and parent_name is not None \
            and not str(parent_name).startswith("AWS-Lab"):
        raise Refuse(EXIT_OWNERSHIP, f"never-touch: {kind} {parent_name!r} does not carry the AWS-Lab prefix")


# --------------------------------------------------------------------------- ZPA entries

def _instance_by_ip(instances, ip):
    if not ip:
        return None
    for i in instances or []:
        if str(i.get("state")) == "terminated":
            continue
        ips = set(i.get("private_ips") or [])
        if i.get("private_ip"):
            ips.add(i["private_ip"])
        if ip in ips:
            return i
    return None


def classify_zpa_entry(rec, kind, lab_group_ids, now, instances, min_age_min=DEFAULT_MIN_AGE_MIN,
                       disconnect_min=DEFAULT_DISCONNECT_MIN, aws_checked=True):
    """kind: 'connector' | 'serviceEdge'. Returns (verdict, reason, facts) with verdict one of
    'PRUNE', 'KEEP', 'SKIP' (SKIP = not lab-owned; never logged per object, never touched).
    `instances` is the list of non-terminated EC2 instances tagged with the lab's Project tag
    ({id, state, private_ip, private_ips}); when aws_checked is False the caller has opted
    out with --no-aws-check and the instance clause is recorded as unchecked."""
    gkey = "appConnectorGroupId" if kind == "connector" else "serviceEdgeGroupId"
    gname = "appConnectorGroupName" if kind == "connector" else "serviceEdgeGroupName"
    gid = sid(rec.get(gkey))
    facts = {"id": sid(rec.get("id")), "group_id": gid, "group": rec.get(gname), "name": rec.get("name"),
             "status": rec.get("controlChannelStatus"), "key": sid(rec.get("provisioningKeyId")),
             "private_ip": rec.get("privateIp")}
    if gid not in lab_group_ids:
        return "SKIP", "not-lab-owned", facts
    if not str(facts["group"] or lab_group_ids[gid]).startswith("AWS-Lab"):
        return "KEEP", "group-name-without-AWS-Lab-prefix", facts      # belt and braces
    created = epoch(rec.get("creationTime")) or epoch(rec.get("enrollmentTime"))
    facts["created"] = fmt_age(now - created) if created else "?"
    last_act = max([t for t in (epoch(rec.get("lastBrokerDisconnectTime")), epoch(rec.get("lastBrokerConnectTime"))) if t] or [0])
    facts["disconnected"] = fmt_age(now - last_act) if last_act else "never"
    if facts["status"] == AUTH:
        return "KEEP", "authenticated", facts
    if created is None:
        return "KEEP", "no-creation-time", facts
    if now - created < min_age_min * 60:
        return "KEEP", f"younger-than-{min_age_min}m", facts
    if last_act and now - last_act < disconnect_min * 60:
        return "KEEP", f"broker-activity-within-{disconnect_min}m", facts
    if aws_checked:
        inst = _instance_by_ip(instances, facts["private_ip"])
        if inst:
            facts["instance"] = f"{inst.get('id')} {inst.get('state')}"
            return "KEEP", f"instance {inst.get('id')} {inst.get('state')}", facts
        facts["instance"] = "none"
    else:
        facts["instance"] = "unchecked"
    return "PRUNE", "stale", facts


# --------------------------------------------------------------------------- ZTW groups / VMs

def is_lab_ecgroup(g, template_name, template_id=None, loc_prefix="AWS-Lab-ZCC-"):
    """('lab' | 'prod' | 'ambiguous', reason) for one ecgroup record. Lab iff deployType
    CLOUD, platform AWS, every VM carries the lab template, location name has the lab prefix;
    with zero VMs the shape left behind after its VM was deleted (zs-cc-vpc-* + lab location)
    still qualifies. Mixed templates inside one group = ambiguous = never touched."""
    name = str(g.get("name") or "")
    loc = str((g.get("location") or {}).get("name") or "")
    vms = g.get("ecVMs") or []
    if str(g.get("deployType")) != "CLOUD" or str(g.get("platform")) != "AWS":
        return "prod", f"deployType={g.get('deployType')} platform={g.get('platform')}"
    if not loc.startswith(loc_prefix):
        return "prod", f"location {loc!r} lacks prefix {loc_prefix!r}"
    if not vms:
        if name.startswith("zs-cc-vpc-"):
            return "lab", "empty CLOUD/AWS group with lab location"
        return "prod", f"empty group name {name!r} lacks zs-cc-vpc- prefix"
    mine, other = 0, 0
    for vm in vms:
        t = vm.get("provTemplate") or {}
        same = str(t.get("name")) == template_name and (template_id is None or sid(t.get("id")) == sid(template_id))
        mine, other = mine + same, other + (not same)
    if mine and not other:
        return "lab", f"{mine} VM(s) on template {template_name!r}"
    if mine and other:
        return "ambiguous", f"{mine} lab VM(s) and {other} foreign VM(s) in one group"
    return "prod", "no VM carries the lab template"


def vm_instance_id(vm, group):
    """EC2 instance id from metaConfig.uuid, else the _<instance-id> suffix of the group desc."""
    u = str((vm.get("metaConfig") or {}).get("uuid") or "")
    if re.fullmatch(r"i-[0-9a-f]{8,17}", u):
        return u
    m = re.search(r"_(i-[0-9a-f]{8,17})$", str(group.get("desc") or ""))
    return m.group(1) if m else None


def classify_cc_vm(vm, group, group_class, template_name, template_id, now, instance_lookup,
                   min_age_min=DEFAULT_MIN_AGE_MIN, aws_checked=True, loc_rule_refs=None):
    """instance_lookup(instance_id) -> None (NotFound) | {'state': ...}. Returns
    (verdict, reason, facts). `loc_rule_refs` is {location_id: [rule refs]} from the ZIA/ZTW
    rule collections only: ZTW refuses to delete the last VM of a group whose location is still
    referenced by a rule (verified at the first prune: INVALID_OPERATION "LOCATION IS ASSOCIATED
    WITH CONNECTOR RULE(S)"), so such a VM is KEPT until the rules move on at the next ON."""
    t = vm.get("provTemplate") or {}
    facts = {"id": sid(vm.get("id")), "group_id": sid(group.get("id")), "group": group.get("name"), "name": vm.get("name"),
             "status": ",".join(vm.get("status") or []) + (f"/{vm.get('operationalStatus')}" if vm.get("operationalStatus") else ""),
             "template": t.get("name")}
    if group_class != "lab":
        return "SKIP", f"group-{group_class}", facts
    if str(t.get("name")) != template_name or (template_id is not None and sid(t.get("id")) != sid(template_id)):
        return "KEEP", f"template {t.get('name')!r} is not the lab template", facts
    loc_id = sid((group.get("location") or {}).get("id"))
    if loc_rule_refs and loc_id in loc_rule_refs and len(group.get("ecVMs") or []) <= 1:
        facts["location"] = loc_id
        return "KEEP", "last VM of a group whose location is referenced by " + ",".join(loc_rule_refs[loc_id]), facts
    reg = max([epoch(i.get("registerTime")) for i in vm.get("ecInstances") or [] if epoch(i.get("registerTime"))] or [0])
    facts["registered"] = fmt_age(now - reg) if reg else "?"
    if not reg:
        return "KEEP", "no-register-time", facts
    if now - reg < min_age_min * 60:
        return "KEEP", f"younger-than-{min_age_min}m", facts
    iid = vm_instance_id(vm, group)
    facts["instance"] = iid or "unknown"
    if not iid:
        return "KEEP", "no-instance-id-on-record", facts
    if not aws_checked:
        facts["instance"] = f"{iid} (unchecked)"
        return "PRUNE", "stale", facts
    inst = instance_lookup(iid)
    if inst is None:
        facts["instance"] = f"{iid} (not found)"
        return "PRUNE", "stale", facts
    if str(inst.get("state")) == "terminated":
        facts["instance"] = f"{iid} terminated"
        return "PRUNE", "stale", facts
    facts["instance"] = f"{iid} {inst.get('state')}"
    return "KEEP", f"instance {iid} {inst.get('state')}", facts


def classify_cc_group(group, group_class, vms_left, group_refs, loc_refs=None):
    """A lab group with zero VMs left (after this run's VM pass), no rule reference to the
    group, and -- because DELETE ecgroup may cascade to its location (unverified) -- no rule
    reference to the group's LOCATION either: the group that carries the location the lab
    rules point at survives until on-post has re-scoped them."""
    facts = {"id": sid(group.get("id")), "name": group.get("name"), "location": (group.get("location") or {}).get("name"),
             "vms_left": vms_left}
    if group_class != "lab":
        return "SKIP", f"group-{group_class}", facts
    refs = group_refs.get(facts["id"]) or []
    if refs:
        return "KEEP", "referenced-by " + ",".join(refs), facts
    lid = sid((group.get("location") or {}).get("id"))
    lrefs = [r for r in (loc_refs or {}).get(lid, []) if not r.startswith("ecgroup/")]
    if lrefs:
        return "KEEP", "location referenced-by " + ",".join(lrefs), facts
    if vms_left:
        return "KEEP", f"{vms_left} VM(s) remain", facts
    return "PRUNE", "empty", facts


# --------------------------------------------------------------------------- ZIA locations

def location_refs(rule_collections, ecgroups, static_groups=(), skip_group_ids=()):
    """{location_id: [ref, ...]} over every rule collection carrying `locations`, every
    ecgroup.location (except groups being deleted in this run) and static location groups."""
    refs = {}

    def add(lid, ref):
        refs.setdefault(sid(lid), []).append(ref)

    for cname, rules in (rule_collections or {}).items():
        for r in rules or []:
            for loc in r.get("locations") or []:
                add(loc.get("id"), f"{cname}/{r.get('id')}")
    for g in ecgroups or []:
        if sid(g.get("id")) in set(sid(x) for x in skip_group_ids):
            continue
        lid = (g.get("location") or {}).get("id")
        if lid is not None:
            add(lid, f"ecgroup/{g.get('id')}")
    for sg in static_groups or []:
        if str(sg.get("groupType")) != "STATIC_GROUP":
            continue
        for loc in sg.get("locations") or []:
            add(loc.get("id"), f"locationGroup/{sg.get('id')}")
    return refs


def classify_location(loc, lab_group_loc_ids, refs, loc_prefix="AWS-Lab-ZCC-"):
    facts = {"id": sid(loc.get("id")), "name": loc.get("name")}
    name = str(loc.get("name") or "")
    if not name.startswith(loc_prefix):
        return "SKIP", "not-lab-owned", facts
    # ccLocation flips to False once the group's last VM is deleted (seen at the first prune);
    # ecLocation stays True for the auto-created root location, so either flag qualifies.
    if not (loc.get("ccLocation") is True or loc.get("ecLocation") is True) or int(loc.get("parentId") or 0) != 0:
        return "KEEP", "lab-prefixed but not a CC/EC root location", facts
    if facts["id"] in refs:
        return "KEEP", "referenced-by " + ",".join(refs[facts["id"]]), facts
    return "PRUNE", "unreferenced", facts


# --------------------------------------------------------------------------- keys

def lab_keys(keys, assoc, lab_group_ids, name_prefix="AWS-Lab"):
    """Keys that are lab-owned by BOTH conditions: name prefix and zcomponentId in a lab group."""
    out = []
    for k in keys or []:
        if str(k.get("name") or "").startswith(name_prefix) and sid(k.get("zcomponentId")) in lab_group_ids:
            out.append({**k, "_assoc": assoc})
    return out


def key_plan(mine, entries_by_key, deleted_entry_ids=(), max_usage=KEY_MAX_USAGE, headroom=KEY_ROTATE_HEADROOM):
    """Housekeeping for the lab's keys. Returns a list of actions in execution order:
      {'op': 'max_usage', 'key': k}                        current key whose maxUsage != 200
      {'op': 'rotate', 'key': k, 'new_name': ...}          headroom below threshold
      {'op': 'delete', 'key': k}                           retired key with no surviving entry
    'current' = the highest version per (assoc, zcomponentId, family base)."""
    fam = {}
    for k in mine:
        base, ver = key_family(k.get("name"))
        fam.setdefault((k["_assoc"], sid(k.get("zcomponentId")), base), []).append((ver, k))
    actions = []
    gone = set(sid(x) for x in deleted_entry_ids)
    for (assoc, zc, base), members in sorted(fam.items()):
        members.sort(key=lambda vk: vk[0])
        cur_ver, cur = members[-1]
        if str(cur.get("maxUsage")) != str(max_usage):
            actions.append({"op": "max_usage", "key": cur, "max_usage": str(max_usage)})
        try:
            left = int(max_usage) - int(cur.get("usageCount") or 0)
        except ValueError:
            left = 0
        if left < headroom:
            actions.append({"op": "rotate", "key": cur, "new_name": key_name(base, cur_ver + 1)})
        for ver, k in members[:-1]:
            kid = sid(k.get("id"))
            live = [e for e in entries_by_key.get(kid, []) if sid(e) not in gone]
            if live:
                actions.append({"op": "keep", "key": k, "reason": f"{len(live)} entry(ies) still enrolled with it"})
            else:
                actions.append({"op": "delete", "key": k})
    return actions


def current_key(keys, base, group_id=None):
    """The highest-version member of a key family (optionally bound to one group), or None."""
    best = None
    for k in keys or []:
        b, v = key_family(k.get("name"))
        if b != base:
            continue
        if group_id is not None and sid(k.get("zcomponentId")) != sid(group_id):
            continue
        if best is None or v > best[0]:
            best = (v, k)
    return best[1] if best else None


# --------------------------------------------------------------------------- guard snapshot

def guard_snapshot(connectors, edges, acgs, segs, lab_cg_ids, lab_se_ids, keys_by_assoc=None,
                   ecgroups=None, lab_ecgroup_ids=(), locations=None, loc_prefix="AWS-Lab-ZCC-",
                   lab_rules=None, ec_rules=None):
    """Member ids of everything the run must NOT change, as sorted lists, comparable with ==.
    Taken before the first write and again after the last; any difference is exit 9."""
    snap = {}
    for g in acgs or []:
        gid = sid(g.get("id"))
        if gid not in lab_cg_ids:
            snap[f"zpa.appConnectorGroup/{gid}"] = sorted(sid(c.get("id")) for c in connectors or [] if sid(c.get("appConnectorGroupId")) == gid)
    for g in segs or []:
        gid = sid(g.get("id"))
        if gid not in lab_se_ids:
            snap[f"zpa.serviceEdgeGroup/{gid}"] = sorted(sid(e.get("id")) for e in edges or [] if sid(e.get("serviceEdgeGroupId")) == gid)
    for assoc, keys in (keys_by_assoc or {}).items():
        snap[f"zpa.key/{assoc}"] = sorted(sid(k.get("id")) for k in keys if sid(k.get("zcomponentId")) not in lab_cg_ids and sid(k.get("zcomponentId")) not in lab_se_ids)
    lab_ec = set(sid(x) for x in lab_ecgroup_ids)
    for g in ecgroups or []:
        gid = sid(g.get("id"))
        if gid not in lab_ec:
            snap[f"ztw.ecgroup/{gid}"] = sorted(sid(vm.get("id")) for vm in g.get("ecVMs") or [])
    if ecgroups is not None:
        snap["ztw.ecgroup"] = sorted(sid(g.get("id")) for g in ecgroups if sid(g.get("id")) not in lab_ec)
    if locations is not None:
        snap["zia.locations"] = sorted(sid(l.get("id")) for l in locations if not str(l.get("name") or "").startswith(loc_prefix))
    for cname, rules in (lab_rules or {}).items():
        for r in rules or []:
            snap[f"zia.{cname}/{r.get('id')}.locations"] = sorted(sid(l.get("id")) for l in r.get("locations") or [])
    for r in ec_rules or []:
        snap[f"ztw.ecRdr/{r.get('id')}.ecGroups"] = sorted(sid(g.get("id")) for g in r.get("ecGroups") or [])
    return snap


def guard_diff(before, after):
    out = []
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            out.append(f"{k}: {before.get(k)} -> {after.get(k)}")
    return out


# --------------------------------------------------------------------------- self-test

def selftest(verbose=True):
    """Synthetic records, including production look-alikes; every assertion must hold."""
    now = 1_800_000_000.0
    LAB_CG = {"1113": "AWS-Lab App Connector Group", "1135": "AWS-Lab ZCC App Connector Group"}
    LAB_SE = {"1112": "AWS-Lab PSE Group"}
    inst = [{"id": "i-stopped", "state": "stopped", "private_ip": "10.91.10.52", "private_ips": ["10.91.10.52"]},
            {"id": "i-running", "state": "running", "private_ip": "10.93.10.10", "private_ips": ["10.93.10.10", "10.93.10.11"]},
            {"id": "i-term", "state": "terminated", "private_ip": "10.91.10.5", "private_ips": ["10.91.10.5"]}]

    def conn(id, gid, gname, status, age_s, disc_s, ip, key="k"):
        return {"id": id, "appConnectorGroupId": gid, "appConnectorGroupName": gname, "controlChannelStatus": status,
                "creationTime": str(int(now - age_s)), "lastBrokerConnectTime": str(int(now - disc_s - 5)),
                "lastBrokerDisconnectTime": str(int(now - disc_s)), "privateIp": ip, "provisioningKeyId": key, "name": f"{gname}-{id}"}

    cases = [
        # production look-alikes: must never be candidates
        (conn("p1", "1100", "Remote-Sites Frankfurt", "ZPN_STATUS_DISCONNECTED", 20 * 86400, 10 * 86400, "172.16.1.5"), "SKIP"),
        (conn("p2", "1048", "OT App Connector Group", "ZPN_STATUS_AUTHENTICATED", 20 * 86400, 0, "10.1.199.155"), "SKIP"),
        # lab group, but younger than 30 min
        (conn("l1", "1113", "AWS-Lab App Connector Group", "ZPN_STATUS_DISCONNECTED", 20 * 60, 16 * 60, "10.91.10.99"), "KEEP"),
        # lab group, old, but a STOPPED instance still holds that private ip (lab.sh stop path)
        (conn("l2", "1113", "AWS-Lab App Connector Group", "ZPN_STATUS_DISCONNECTED", 2 * 86400, 3600, "10.91.10.52"), "KEEP"),
        # lab group, old, running instance
        (conn("l3", "1135", "AWS-Lab ZCC App Connector Group", "ZPN_STATUS_DISCONNECTED", 2 * 86400, 3600, "10.93.10.11"), "KEEP"),
        # lab group, authenticated (anomaly at on-pre, still kept)
        (conn("l4", "1113", "AWS-Lab App Connector Group", "ZPN_STATUS_AUTHENTICATED", 2 * 86400, 0, "10.91.10.7"), "KEEP"),
        # lab group, old, disconnected 10 min ago (reconnect in flight)
        (conn("l5", "1113", "AWS-Lab App Connector Group", "ZPN_STATUS_DISCONNECTED", 2 * 86400, 10 * 60, "10.91.10.8"), "KEEP"),
        # lab group, old, disconnected, instance only terminated -> stale
        (conn("l6", "1113", "AWS-Lab App Connector Group", "ZPN_STATUS_DISCONNECTED", 2 * 86400, 86400, "10.91.10.5", "k1"), "PRUNE"),
        # lab group, never connected, old -> stale
        ({"id": "l7", "appConnectorGroupId": "1113", "appConnectorGroupName": "AWS-Lab App Connector Group",
          "controlChannelStatus": "ZPN_STATUS_UNKNOWN", "creationTime": str(int(now - 7200)), "privateIp": "10.91.10.200", "provisioningKeyId": "k1"}, "PRUNE"),
    ]
    for rec, want in cases:
        v, reason, facts = classify_zpa_entry(rec, "connector", LAB_CG, now, inst)
        assert v == want, f"connector {rec['id']}: got {v} ({reason}), want {want}"
    # service edge: production edge, and a lab edge without AWS data (opted out)
    se_prod = {"id": "s0", "serviceEdgeGroupId": "1031", "serviceEdgeGroupName": "ZPA PSE Group", "controlChannelStatus": AUTH,
               "creationTime": str(int(now - 9e6)), "privateIp": "10.1.199.35"}
    assert classify_zpa_entry(se_prod, "serviceEdge", LAB_SE, now, inst)[0] == "SKIP"
    se_lab = {"id": "s1", "serviceEdgeGroupId": "1112", "serviceEdgeGroupName": "AWS-Lab PSE Group", "controlChannelStatus": "ZPN_STATUS_DISCONNECTED",
              "creationTime": str(int(now - 86400)), "lastBrokerDisconnectTime": str(int(now - 3600)), "privateIp": "10.91.10.155"}
    assert classify_zpa_entry(se_lab, "serviceEdge", LAB_SE, now, None, aws_checked=False)[0] == "PRUNE"
    # a disconnected entry inside a never-touch group is SKIP even if the caller mislabels the group as lab
    assert classify_zpa_entry(conn("x", "1100", "Remote-Sites Frankfurt", "ZPN_STATUS_DISCONNECTED", 9e6, 9e5, "1.2.3.4"),
                              "connector", {"1100": "Remote-Sites Frankfurt"}, now, inst)[0] == "KEEP"

    # never-touch / anchors
    try:
        assert_not_never_touch("zpa.appConnectorGroup", "72063920524755100", "Remote-Sites Frankfurt")
        raise AssertionError("never-touch parent accepted")
    except Refuse as e:
        assert e.code == EXIT_OWNERSHIP
    present = {k: dict(v) for k, v in NEVER_TOUCH.items()}
    assert check_anchors(present, ("zpa", "ztw", "zia")) == []
    present["zpa.appConnectorGroup"].pop("72063920524755106")
    assert any("missing" in p for p in check_anchors(present, ("zpa",)))
    present["zpa.appConnectorGroup"]["72063920524755106"] = "Renamed"
    assert any("renamed" in p for p in check_anchors(present, ("zpa",)))
    try:
        resolve_lab_groups([{"id": "1", "name": "AWS-Lab App Connector Group"}, {"id": "2", "name": "AWS-Lab App Connector Group"}], [], ["AWS-Lab App Connector Group"])
        raise AssertionError("ambiguous group accepted")
    except Refuse as e:
        assert e.code == EXIT_OWNERSHIP
    assert resolve_lab_groups([], [], ["AWS-Lab App Connector Group"]) == {"cg": {}, "se": {}}

    # ZTW groups and VMs
    TPL, TID = "AWS-Lab-ZCC-AWS-small", "6641588"
    prod_g = {"id": "195093841", "name": "Branch2-Frankfurt", "deployType": "ONPREM", "platform": "REDHAT_LINUX",
              "location": {"id": "195093073", "name": "Frankfurt-ZTB"}, "desc": "ZTB - clusterID 214",
              "ecVMs": [{"id": "v0", "name": "Branch2-Frankfurt-VM-a", "status": ["REGISTERED"], "operationalStatus": "INACTIVE",
                         "provTemplate": {"id": "6369665", "name": "ZTBPL-x"}, "metaConfig": {"uuid": "195093844-6"},
                         "ecInstances": [{"id": "1", "registerTime": str(int(now - 9e6))}]}]}
    lab_g = {"id": "198424715", "name": "zs-cc-vpc-0b5d-eu-central-1a", "deployType": "CLOUD", "platform": "AWS",
             "location": {"id": "198424712", "name": "AWS-Lab-ZCC-eu-central-1-vpc-0b5d"}, "desc": "Auto created from ami-1_i-0000000000000dead",
             "ecVMs": [{"id": "v1", "name": "zs-cc-vpc-0b5d-eu-central-1a-VM-R", "status": ["REGISTERED", "PKG_REPO_REGISTERED"], "operationalStatus": "INACTIVE",
                        "provTemplate": {"id": TID, "name": TPL}, "metaConfig": {"uuid": "i-0000000000000dead"},
                        "ecInstances": [{"id": "2", "registerTime": str(int(now - 7200))}]}]}
    live_g = {**lab_g, "id": "198426116", "name": "zs-cc-vpc-01e4-eu-central-1a", "location": {"id": "198426114", "name": "AWS-Lab-ZCC-eu-central-1-vpc-01e4"},
              "ecVMs": [{**lab_g["ecVMs"][0], "id": "v2", "metaConfig": {"uuid": "i-00000000000001ab"}}]}
    young_g = {**lab_g, "id": "9", "ecVMs": [{**lab_g["ecVMs"][0], "id": "v3", "ecInstances": [{"id": "3", "registerTime": str(int(now - 600))}]}]}
    mixed_g = {**lab_g, "id": "10", "ecVMs": [lab_g["ecVMs"][0], prod_g["ecVMs"][0]]}
    empty_g = {**lab_g, "id": "11", "ecVMs": []}
    empty_prod = {**prod_g, "ecVMs": []}
    assert is_lab_ecgroup(prod_g, TPL, TID)[0] == "prod"
    assert is_lab_ecgroup(lab_g, TPL, TID)[0] == "lab"
    assert is_lab_ecgroup(mixed_g, TPL, TID)[0] == "ambiguous"
    assert is_lab_ecgroup(empty_g, TPL, TID)[0] == "lab"
    assert is_lab_ecgroup(empty_prod, TPL, TID)[0] == "prod"
    assert is_lab_ecgroup({**lab_g, "location": {"id": "1", "name": "Zaandam"}}, TPL, TID)[0] == "prod"
    ec2 = {"i-00000000000001ab": {"state": "running"}, "i-00000000000002ab": {"state": "stopped"}}
    look = lambda iid: ec2.get(iid)  # noqa: E731
    assert classify_cc_vm(prod_g["ecVMs"][0], prod_g, "prod", TPL, TID, now, look)[0] == "SKIP"
    assert classify_cc_vm(lab_g["ecVMs"][0], lab_g, "lab", TPL, TID, now, look)[0] == "PRUNE"
    assert classify_cc_vm(live_g["ecVMs"][0], live_g, "lab", TPL, TID, now, look)[0] == "KEEP"
    stop_vm = {**lab_g["ecVMs"][0], "metaConfig": {"uuid": "i-00000000000002ab"}}
    assert classify_cc_vm(stop_vm, lab_g, "lab", TPL, TID, now, look)[0] == "KEEP"
    assert classify_cc_vm(young_g["ecVMs"][0], young_g, "lab", TPL, TID, now, look)[0] == "KEEP"
    assert classify_cc_vm(lab_g["ecVMs"][0], lab_g, "ambiguous", TPL, TID, now, look)[0] == "SKIP"
    assert vm_instance_id({"metaConfig": {"uuid": "195093844-6"}}, lab_g) == "i-0000000000000dead"
    assert classify_cc_group(empty_g, "lab", 0, {})[0] == "PRUNE"
    assert classify_cc_group(empty_g, "lab", 1, {})[0] == "KEEP"
    assert classify_cc_group(empty_g, "lab", 0, {"11": ["ecRdr/5"]})[0] == "KEEP"
    assert classify_cc_group(empty_prod, "prod", 0, {})[0] == "SKIP"
    # the empty group whose location the lab rules still point at is kept (cascade hazard)
    assert classify_cc_group(empty_g, "lab", 0, {}, {"198424712": ["urlFilteringRules/2177083", "ecgroup/11"]})[0] == "KEEP"
    assert classify_cc_group(empty_g, "lab", 0, {}, {"198424712": ["ecgroup/11"]})[0] == "PRUNE"

    # ZIA locations: the one the rules point at survives; the group-referenced one survives
    # until its group is deleted in the same run; production locations are never candidates
    rules = {"urlFilteringRules": [{"id": "2177083", "locations": [{"id": "198426114"}]}],
             "firewallFilteringRules": [{"id": "1918030", "locations": [{"id": "195093073"}]}]}
    locs = [{"id": "198426114", "name": "AWS-Lab-ZCC-eu-central-1-vpc-01e4", "ccLocation": True, "ecLocation": True, "parentId": 0},
            {"id": "198424712", "name": "AWS-Lab-ZCC-eu-central-1-vpc-0b5d", "ccLocation": True, "ecLocation": True, "parentId": 0},
            {"id": "195093073", "name": "Frankfurt-ZTB", "ccLocation": False, "ecLocation": True, "parentId": 0},
            {"id": "195126546", "name": "ORG_DEFAULT", "ccLocation": False, "ecLocation": False, "parentId": 0}]
    refs = location_refs(rules, [lab_g, live_g, prod_g])
    got = {l["id"]: classify_location(l, {"198424712", "198426114"}, refs)[0] for l in locs}
    assert got == {"198426114": "KEEP", "198424712": "KEEP", "195093073": "SKIP", "195126546": "SKIP"}, got
    refs2 = location_refs(rules, [lab_g, live_g, prod_g], skip_group_ids=["198424715"])
    assert classify_location(locs[1], {"198424712", "198426114"}, refs2)[0] == "PRUNE"
    assert classify_location(locs[0], {"198424712", "198426114"}, refs2)[0] == "KEEP"
    # a lab-prefixed location that is not a CC root location is kept, not pruned
    assert classify_location({**locs[1], "parentId": 5}, set(), {})[0] == "KEEP"

    # keys
    assert key_family("AWS-Lab CONNECTOR_GRP key") == ("AWS-Lab CONNECTOR_GRP key", 1)
    assert key_family("AWS-Lab CONNECTOR_GRP key v2") == ("AWS-Lab CONNECTOR_GRP key", 2)
    assert key_name("AWS-Lab CONNECTOR_GRP key", 3) == "AWS-Lab CONNECTOR_GRP key v3"
    keys = [{"id": "170196", "name": "AWS-Lab CONNECTOR_GRP key", "zcomponentId": "1113", "maxUsage": "5", "usageCount": "1"},
            {"id": "172048", "name": "AWS-Lab CONNECTOR_GRP key v2", "zcomponentId": "1113", "maxUsage": "25", "usageCount": "3"},
            {"id": "123347", "name": "OT App-Connector-ZTB", "zcomponentId": "1048", "maxUsage": "20", "usageCount": "1"},
            {"id": "9", "name": "AWS-Lab impostor", "zcomponentId": "1048", "maxUsage": "5", "usageCount": "0"},
            {"id": "170199", "name": "AWS-Lab X key", "zcomponentId": "1113", "maxUsage": "200", "usageCount": "197"}]
    mine = lab_keys(keys, "CONNECTOR_GRP", LAB_CG)
    assert sorted(k["id"] for k in mine) == ["170196", "170199", "172048"], [k["id"] for k in mine]
    plan = key_plan(mine, {"170196": ["l6"]})
    ops = [(a["op"], a["key"]["id"]) for a in plan]
    assert ("max_usage", "172048") in ops and ("keep", "170196") in ops and ("rotate", "170199") in ops, ops
    assert not any(op == "delete" for op, _ in ops)
    plan = key_plan(mine, {"170196": ["l6"]}, deleted_entry_ids=["l6"])
    assert ("delete", "170196") in [(a["op"], a["key"]["id"]) for a in plan]
    assert [a["new_name"] for a in plan if a["op"] == "rotate"] == ["AWS-Lab X key v2"]
    assert current_key(keys, "AWS-Lab CONNECTOR_GRP key", "1113")["id"] == "172048"
    assert current_key(keys, "AWS-Lab CONNECTOR_GRP key", "9999") is None

    # guard snapshot: a lab deletion does not change it, a production change does
    conns = [conn("p1", "1100", "Remote-Sites Frankfurt", "ZPN_STATUS_DISCONNECTED", 9e6, 9e5, "172.16.1.5"),
             conn("l6", "1113", "AWS-Lab App Connector Group", "ZPN_STATUS_DISCONNECTED", 9e6, 9e5, "10.91.10.5")]
    acgs = [{"id": "1100", "name": "Remote-Sites Frankfurt"}, {"id": "1113", "name": "AWS-Lab App Connector Group"}]
    before = guard_snapshot(conns, [], acgs, [], LAB_CG, LAB_SE, ecgroups=[prod_g, lab_g], lab_ecgroup_ids=["198424715"], locations=locs, lab_rules=rules)
    after = guard_snapshot(conns[:1], [], acgs, [], LAB_CG, LAB_SE, ecgroups=[prod_g, empty_g], lab_ecgroup_ids=["198424715", "11"], locations=locs[:1] + locs[2:], lab_rules=rules)
    assert guard_diff(before, after) == [], guard_diff(before, after)
    after2 = guard_snapshot(conns[1:], [], acgs, [], LAB_CG, LAB_SE, ecgroups=[prod_g, lab_g], lab_ecgroup_ids=["198424715"], locations=locs, lab_rules=rules)
    assert guard_diff(before, after2) == ["zpa.appConnectorGroup/1100: ['p1'] -> []"]
    assert fmt_age(2 * 86400 + 3 * 3600) == "2d3h" and fmt_age(45 * 60) == "45m" and fmt_age(3 * 3600 + 12 * 60) == "3h12m"
    if verbose:
        print("prune_lib selftest: 9 connector cases, 2 service-edge cases, anchors, 6 ecgroup shapes, 6 VM cases, "
              "6 group cases, 5 location cases, key families/plan, guard snapshot -- all assertions hold")
    return True


if __name__ == "__main__":
    selftest()
