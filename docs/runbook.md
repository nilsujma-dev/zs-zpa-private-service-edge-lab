# Runbook

## Enrolment: four silent failure modes

Provisioning-key enrolment fails **without an error message** in all four cases
below. The instance boots, the service runs, and it quietly falls back to
interactive OAuth — printing a user code on the console and waiting forever.
Each of these cost a full rebuild cycle to find.

### 1. The key file must be mode 644

```sh
echo "$KEY" > /opt/zscaler/var/provision_key
chmod 644 /opt/zscaler/var/provision_key      # NOT 600
```

The connector/edge service does not run as root and cannot read a `600` file.
Written with `echo`, so it carries a trailing newline — matching Zscaler's own
`user_data_rhel9.sh`.

### 2. Stop the service *first*, before anything slow

The AMI auto-starts the service at boot. If it is still running while you install
the AWS CLI and poll SSM (~60s), it commits to OAuth2 provisioning and then
ignores the key file entirely — logging:

```
[OAuth] Started provisioning this device using OAuth2.0 method
```

…while a perfectly valid 427-byte key sits unread beside it. Zscaler's own script
gets away with stopping later only because it embeds the key inline and stops
within seconds of boot. Anything that *fetches* the key must stop up front, then
clear any OAuth state established in the meantime:

```sh
systemctl stop "$SVC"          # first action in user_data
# ... install CLI, fetch key from SSM ...
rm -f /opt/zscaler/var/oauth_enrollment_stats.json \
      /opt/zscaler/var/instance_id.bin /opt/zscaler/var/instance_id.crypt
systemctl start "$SVC"
sleep 60; systemctl stop "$SVC"; systemctl start "$SVC"
```

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

The tenant used for this lab is shared with production, so every script here is
built to prove it changed nothing else.

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
