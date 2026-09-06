# Runbook

## Enrolment: five silent failure modes

Provisioning-key enrolment fails **without an error message** in all five cases
below. The instance boots, the service runs, and it quietly falls back to
interactive OAuth — printing a user code on the console and waiting forever.
Each of these cost a full rebuild cycle to find.

### 0. The two components read the key from DIFFERENT paths

This is the one that costs the most time, because everything looks correct.

| Component | Path |
|---|---|
| App Connector | `/opt/zscaler/var/provision_key` |
| **Service Edge** | **`/opt/zscaler/var/service-edge/provision_key`** |

Write the connector's flat path on a Service Edge and the file is created
perfectly — mode 644, right byte count, owned by root — in a location the edge
never reads. It falls through to OAuth with no complaint. `ls /opt/zscaler/var`
shows both `provision_key` and a `service-edge/` directory, which is the tell.

Each module ships **two** bootstrap scripts and only one is relevant:

| Script | For |
|---|---|
| `scripts/user_data_rhel9.sh` | a plain RHEL9 base — adds Zscaler's yum repo and installs the package. Uses the flat path for both components. |
| `scripts/user_data_zscaler.sh` | **the Marketplace AMI** — package pre-baked. This is the one that applies here, and the one with the split paths. |

Read the raw bytes, not a summary:

```sh
gh api repos/zscaler/terraform-aws-zpa-private-service-edge-modules/contents/scripts/user_data_zscaler.sh \
  --jq '.content' | base64 -d
```

### 1. The key file must be mode 644

```sh
echo "$KEY" > /opt/zscaler/var/provision_key
chmod 644 /opt/zscaler/var/provision_key      # NOT 600
```

The connector/edge service does not run as root and cannot read a `600` file.
Written with `echo`, so it carries a trailing newline — matching Zscaler's own
`user_data_rhel9.sh`.

### 2. Stop the service before writing the key

The AMI auto-starts the service at boot, so stop it before writing. Note that a
running service is *not* by itself the cause of an OAuth fallback — this was
initially misdiagnosed as an ordering problem when the real fault was the key
path above. Stop, write, start is sufficient; the extra settle-and-restart cycle
in `user_data_rhel9.sh` is not needed on the AMI. The symptom to recognise is:

```
[OAuth] Started provisioning this device using OAuth2.0 method
```

…while a perfectly valid 427-byte key sits unread. Seeing this line means the
edge did not find a key **where it looked** — check the path in §0 first.

What the shipped bootstrap does, matching `user_data_zscaler.sh`:

```sh
systemctl stop "$SVC"
install -d -m 755 "$(dirname "$KEYPATH")"   # service-edge/ may not exist yet
echo "$KEY" > "$KEYPATH"
chmod 644 "$KEYPATH"
systemctl start "$SVC"
```

With the correct path this enrols in about three minutes from boot: roughly one
minute of instance start plus CLI install and SSM fetch, then the edge registers
and `usageCount` on the provisioning key increments.

### 3. Never send `enrollmentCertId` and `signingCertId` together

The ZPA API silently resolves `enrollmentCertId` to the **signing** cert when both
are present. No error, wrong cert, no indication anything went wrong.

App Connectors tolerate the Root cert and enrol anyway — which *masks* the bug.
Service Edges reject it and fall back to OAuth. Always read the object back and
assert the cert landed; `scripts/zpa_fix_se_key.py` does exactly this.

### 4. The group and the key use different certs

A working Service Edge Group pairs:

| Object | `enrollmentCertId` |
|---|---|
| Service Edge **Group** | Connector cert |
| Provisioning **key** | Service Edge cert |

Setting both to the Service Edge cert does not work. Compare against a known-good
group in the tenant before assuming.

## Diagnosing an instance you cannot log into

The ZPA AMIs ship **without the SSM agent**, and this lab sets no key pair — so a
failing instance cannot be inspected from outside. Two consequences:

**EC2 console output is not a usable channel.** It holds only the last 64KB, and
the OAuth banner repeats every few seconds, flushing anything useful within
minutes. Bootstrap output *will* appear there for the first few minutes and then
vanish — do not conclude from its absence that `user_data` failed to run. Check
`describe-instance-attribute --attribute userData` and the cloud-init finish
timestamp instead.

**So the instance reports on itself.** The bootstrap publishes its own state to
`/zpa-lab/debug/<hostname>` — key file mode and byte count, service active/enabled,
zpa units present, `/etc/issue` OAuth code if any, and the last 12 journal lines.
It never publishes the key, only its length. Read it with:

```sh
aws ssm get-parameter --region eu-central-1 \
  --name /zpa-lab/debug/<hostname> --query 'Parameter.Value' --output text | tr '|' '\n'
```

This requires `ssm:PutParameter` scoped to `/zpa-lab/debug/*` on the instance role.

## ZPA safety model

This lab is self-contained apart from one thing: the **ZPA tenant**. A tenant is
an account-level object, so if the credentials you hand it point at a tenant that
already carries other work, this lab's objects land beside that work. Nothing else
is shared — the VPCs, subnets, routing, instances, Service Edge Group, App
Connector Groups and provisioning keys are all created here and prefixed
`AWS-Lab`.

Every script is therefore built to prove it changed nothing it did not create.
Against an empty tenant this machinery is redundant; against a populated one it
is the difference between a safe lab and an incident.

- **Create-only.** No `PUT`, no `DELETE`, with two exceptions:
  `scripts/zpa_fix_se_group.py`, hard-guarded to a single group ID and refusing to
  run if the object's name does not match; and `scripts/prune.py`, which deletes
  only inside the three `AWS-Lab` groups under the rules in "Pruning stale
  entries" below and is dry-run unless told `--apply`.
- **Baseline before writing.** `scripts/zpa_snapshot.py` captures a SHA-hashed
  snapshot of every object class that could conceivably be touched.
- **Verify after.** `scripts/zpa_verify.py` re-reads live state, excludes objects
  this automation owns (`AWS-Lab*` by name, ids in `zpa-created.json`), scrubs the
  volatile fields (the `connectors`/`serviceEdges` lists inside a group, key
  `usageCount`, timestamps) and asserts the remainder is identical to the
  baseline. Lab-owned groups are compared by identity, not member count, so a
  pruned group or a rotated key is not a difference.

Snapshot output is git-ignored — it contains live tenant topology.

## Rebuild economics

Each `down` → `up` cycle consumes one use of each provisioning key and leaves a
stale disconnected entry in ZPA. Two things follow:

- At the default `maxUsage` of 5, the sixth rebuild **silently falls back to
  OAuth**. The lab keys run at 200 and `prune.py` mints the next version of a key
  when fewer than 5 uses remain (see below), so this no longer needs a hand.
- Stale entries are pruned by `prune.py` at OFF and again before ON — only inside
  the `AWS-Lab *` groups, never by name or status. Nothing accumulates.

## Pruning stale entries

`scripts/prune.py` (`./lab.sh prune` = dry-run, `./lab.sh prune --apply` = write).
Switchboard's manifest runs `--phase off --apply` after `tofu destroy` and
`--phase on-pre --apply` after the create scripts, before `put_keys_ssm.py`. The
rule module `scripts/prune_lib.py` is byte-identical to the Cloud Connector lab's;
edit one, copy to the other.

**Ownership rule.** An entry is lab-owned only if the tenant says so through group
membership resolved from an exact name: `appConnectorGroupId` is the id of
`AWS-Lab App Connector Group` or `AWS-Lab PRIV Connector Group`, or
`serviceEdgeGroupId` is the id of `AWS-Lab PSE Group`. A provisioning key is
lab-owned when its name starts with `AWS-Lab` **and** its `zcomponentId` is one of
those three groups. Names alone decide nothing (the tenant generates them), status
alone decides nothing (production has `ZPN_STATUS_DISCONNECTED` connectors in
`Remote-Sites Frankfurt` and `Remote Site Zaandam`). Two groups of one lab name
is a hard stop (exit 8).

**Never-touch list**, asserted before any write (`prune_lib.NEVER_TOUCH`; a
missing or renamed anchor means the wrong tenant and exits 8): Service Edge Group
`ZPA PSE Group` and its edge; App Connector Groups `OT-EBC AppConnector Group`,
`OT App Connector Group`, `Remote-Sites Brussels`, `Remote-Sites Frankfurt`,
`Remote Site Zaandam` — and by rule every group whose name does not start with
`AWS-Lab`; every provisioning key bound to a non-lab group (`OT App-Connector-ZTB`,
`IT App-Connector-ZTB`, `ZPA PSE Key`, `ZTB-*`, …). A candidate whose parent is on
the list, or a run whose guard snapshot of those groups' member ids differs after
the writes, exits 8 / 9.

**Stale** (every clause must hold): lab-owned; `controlChannelStatus` is not
`ZPN_STATUS_AUTHENTICATED`; `creationTime` older than `--min-age-min` (30); no
broker connect/disconnect activity for 15 min; **no EC2 instance tagged
`Project=zpa-pse-lab` in any non-terminated state holds the entry's `privateIp`**
— that clause is what keeps a `lab.sh stop`ped instance's entry. Without AWS
credentials the script refuses (exit 2) unless `--no-aws-check` is given. Anything
else is logged `KEEP … reason=` and left alone; an AUTHENTICATED entry in a lab
group while the lab is off is reported, not deleted.

**Order** inside one run: connectors → service edges → keys → guard re-read. ZPA
needs no activation. Every object gets one line before and after:

```
PRUNE zpa.connector id=72063920524755116 group="AWS-Lab App Connector Group" name="AWS-Lab CONNECTOR_GRP key-1788536744433" status=ZPN_STATUS_DISCONNECTED created=2d3h disconnected=1d0h key=170196 instance=none -> DELETE ok
KEEP  zpa.serviceEdge id=… reason=instance i-0abc stopped
```

Nothing credential-shaped is ever printed. A 404 on DELETE is "already gone"; a
429/5xx leaves the entry for the next phase, counted under `status.py`'s `stale`,
and the job still exits 0 — only safety violations fail it.

**Keys.** For the current key of each family (`AWS-Lab SERVICE_EDGE_GRP key`,
`AWS-Lab CONNECTOR_GRP key`, `AWS-Lab PRIV CONNECTOR_GRP key`; current = highest
`v<N>` bound to the lab group) the script PUTs `maxUsage 200` (GET → drop
`signingCertId`, the key value and timestamps → PUT → GET → assert cert, group,
enabled, maxUsage). When `maxUsage - usageCount < 5` it mints `<family> v<N+1>` on
the same group with the same cert — Root for connector keys, **Service Edge (31031)
for the PSE key, never sending `signingCertId`** (failure modes 3 and 4 above) —
reads it back, seeds the matching `/zpa-lab/*` SSM parameter and points
`zpa-created.json` at it. `zpa_create.py` and `put_keys_ssm.py` resolve the current
key by family, so the next ON picks the new key up by name. Retired keys (older
versions) are deleted once no entry is enrolled with them; the retired v1 keys
`AWS-Lab SERVICE_EDGE_GRP key`, `AWS-Lab CONNECTOR_GRP key`, `AWS-Lab PRIV
CONNECTOR_GRP key` go on the first run that also removes the two 09-04 connectors.

**Caps and exit codes.** `--max N` (default 12 = three components × four sets)
refuses with exit 7 when more entries than that would be deleted; `--first N`
processes only the N oldest candidates and `DEFER`s the rest; `--only
connector|service_edge|keys` restricts the type. Exit 8 = ownership, never-touch,
anchor or ambiguity; exit 9 = the guard snapshot differs after the run, or a key
read-back differs from what was written; exit 3 = `ZCC_READ_ONLY=1` is set and
`--apply` was asked. `--selftest` runs the rule offline against synthetic records
including production look-alikes (a disconnected connector in a non-lab group, a
disconnected lab entry younger than 30 min, an entry whose instance is stopped).

**First supervised `--apply` — do this once before the manifests carry `--apply`.**
All of it with the portal's Connectors / Service Edges / Provisioning Keys pages
open and AWS credentials exported (never `--no-aws-check` here):

1. `python3 scripts/prune.py` — read the full dry-run; every `PRUNE` line must
   name one of the three `AWS-Lab` groups, and `pruned:` must match what you see
   in the portal.
2. `python3 scripts/prune.py --apply --max 1 --first 1 --only connector` — one
   connector. Note the `usageCount` of its key before and after (does deletion
   refund a use?) and that the portal's other groups did not move. Record the
   answer here.
3. `python3 scripts/prune.py --apply --max 1 --first 1 --only service_edge` —
   one edge, same checks.
4. `python3 scripts/prune.py --apply --only keys` — the `PUT maxUsage 200` lines;
   each is read back and asserted. Whether the PUT accepts the partial body and
   whether 200 is accepted are the two things this step proves.
5. `python3 scripts/prune.py --apply` — the remainder; then `zpa_verify.py`.

Then flip `--apply` on in the Switchboard manifest. Findings to record: refund on
delete (yes/no), PUT body accepted (yes/no), whether a retired key's deletion had
any effect on the tenant beyond the key itself (expected: none).

## Cost

Verified against the AWS Pricing API, `eu-central-1`, always-on:

| | $/month |
|---|---|
| PSE `m5.large` | 83.95 |
| App Connector ×2 `t3.medium` | 70.08 |
| MCU client `t3.medium` Windows | 48.47 |
| NAT gateway | 37.96 |
| EBS gp3 298 GiB | 28.37 |
| nginx `t3.micro` | 8.76 |
| Public IPv4 ×2 | 7.30 |
| **Total** | **~285** plus NAT data processing |

`./lab.sh stop` leaves ~$74/mo running: the NAT gateway, elastic IPs and volumes
persist. Only `./lab.sh down` approaches zero.

Regenerate with `python3 scripts/cost.py` — it queries the Pricing API rather than
hardcoding rates. Note that Windows must be matched on
`licenseModel = "No License required"`; taking the cheapest matching SKU returns
the infrastructure-only component and understates the bill by ~30%.
