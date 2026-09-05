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

- **Create-only.** No `PUT`, no `DELETE`, with one exception:
  `scripts/zpa_fix_se_group.py`, hard-guarded to a single group ID and refusing to
  run if the object's name does not match.
- **Baseline before writing.** `scripts/zpa_snapshot.py` captures a SHA-hashed
  snapshot of every object class that could conceivably be touched.
- **Verify after.** `scripts/zpa_verify.py` re-reads live state, excludes objects
  this automation created, and asserts the remainder is byte-identical to the
  baseline.

Snapshot output is git-ignored — it contains live tenant topology.

## Rebuild economics

Each `down` → `up` cycle consumes one use of each provisioning key and leaves a
stale disconnected entry in ZPA. Two things follow:

- Raise `maxUsage` before running long automation. At the default of 5, the sixth
  rebuild **silently falls back to OAuth**. The Service Edge key here is set to 25.
- Stale entries accumulate one per rebuild. Removing them is a `DELETE` against
  ZPA, deliberately not scripted — do it in the console where the blast radius is
  visible, and only under the `AWS-Lab *` groups.

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
