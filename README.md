# zpa-aws-lab

A reproducible AWS lab for a **Zscaler Private Access Private Service Edge**, built
with OpenTofu and stdlib Python. Two isolated VPCs, three network zones, unattended
enrolment, and a single script to spin the whole thing up or tear it down.

No vendor licensing. The ZPA AMIs carry no marketplace product code and bill as
plain `RunInstances`, so the only cost is EC2 infrastructure.

## Topology

```
                         internet / Zscaler cloud
                                   |
        ┌──────────────────────────┼──────────────────────────┐
        │  VPC A · isolated        │        VPC B · private    │
        │  10.91.0.0/16            │        10.90.0.0/16       │
        │                          │                           │
        │  ┌────────────────┐      │   ┌──────────────────┐    │
        │  │ PSE            │◄─────┼───│ NAT gateway      │    │
        │  │ elastic IP     │      │   └────────┬─────────┘    │
        │  │ App Connector  │      │            │              │
        │  └────────────────┘      │   ┌────────┴─────────┐    │
        │                          │   │ PRIV  10.90.20/24│    │
        │                          │   │ connector + nginx│    │
        │                          │   └────────╳─────────┘    │
        │                          │        no direct path     │
        │                          │   ┌────────┴─────────┐    │
        │                          │   │ MCU   10.90.30/24│    │
        │                          │   │ Windows client   │    │
        │                          │   └──────────────────┘    │
        └──────────────────────────┴───────────────────────────┘
```

**VPC A** holds the Private Service Edge and a co-located App Connector. It is
genuinely isolated — no peering, no transit gateway, no shared route table. The
two VPCs could sit in different accounts or regions and nothing would change.

**VPC B** holds two zones that cannot reach each other. `PRIV` runs an App
Connector and an nginx server; `MCU` runs a client. The client can reach the
server *only* through ZPA brokering — a direct connection fails. That block is
enforced twice: by security-group omission and by a subnet network ACL, so it
survives someone loosening a security group by mistake.

Because there is no peering, security groups cannot reference each other across
VPCs. The PSE instead accepts `443` from exactly one address — VPC B's NAT
elastic IP. Tighter than a peered rule, not looser.

## Layout

| Path | What it is |
|---|---|
| `terraform/main.tf` | VPC A, IAM, the shared bootstrap, PSE + connector |
| `terraform/vpc_b.tf` | VPC B, PRIV and MCU zones, NACLs, NAT, cross-VPC rule |
| `lab.sh` | lifecycle: `status`, `stop`, `start`, `up`, `down`, `keys` |
| `scripts/` | ZPA object creation, key→SSM seeding, status, cost, verification |
| `discovery/` | one-off probes: AMI availability, instance-type support, quotas |

## Enrolment is unattended

Each VM pulls its provisioning key from **SSM Parameter Store** at boot and
self-enrols. No OAuth code, no console login, nothing interactive. The key value
never touches disk and never enters OpenTofu state — `user_data` contains only a
parameter *name*, and the instance reads it via its IAM role.

### Four things that will silently break this

Every one of these fails without an error message. They are recorded here because
each cost a full rebuild cycle to find.

1. **`/opt/zscaler/var/provision_key` must be mode `644`, not `600`.**
   The service does not run as root and cannot read a `600` file.

2. **Stop the service *first*, before anything slow.**
   The image auto-starts the connector/edge at boot. If it is still running while
   you install the AWS CLI and poll SSM (~60s), it commits to OAuth2 provisioning
   and then ignores the key file entirely — logging
   `[OAuth] Started provisioning this device using OAuth2.0 method` while a
   perfectly valid key sits unread beside it. Zscaler's own script gets away with
   stopping later only because it embeds the key inline and stops within seconds.

3. **Never send `enrollmentCertId` and `signingCertId` together to the ZPA API.**
   It silently resolves `enrollmentCertId` to the *signing* cert. No error, wrong
   cert. App Connectors tolerate the Root cert and will enrol anyway, masking the
   bug; Service Edges reject it and fall back to OAuth. Always read the object
   back and assert the cert landed — `zpa_fix_se_key.py` does.

4. **The group and the key use *different* certs.**
   A working Service Edge Group pairs group `enrollmentCertId` = Connector cert
   with key `enrollmentCertId` = Service Edge cert. Setting both to Service Edge
   does not work.

EC2 console output is not a usable diagnostic channel here: it holds only the last
64KB and the OAuth banner floods it within minutes. The ZPA AMIs also ship without
the SSM agent, so a failing instance cannot be inspected from outside. The
bootstrap therefore publishes its own state to `/zpa-lab/debug/<hostname>` — key
file mode and size, service status, journal tail — never the key itself.

## Usage

```bash
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_SESSION_TOKEN=...
export ZS_ISSUER="https://<tenant>.zslogin.net"
export ZS_CLIENT_ID="<oneapi client id>"
# client secret is read from ~/.zscaler_api_key

python3 scripts/zpa_create.py        # service edge group, connector group, keys
python3 scripts/zpa_create_priv.py   # PRIV connector group + key
python3 scripts/put_keys_ssm.py      # keys -> SSM SecureString (never to disk)
./lab.sh up                          # plan, review, apply
./lab.sh status                      # instances + ZPA enrolment
```

`./lab.sh stop` parks the VMs (~$74/mo — NAT, EIPs and volumes persist).
`./lab.sh down` destroys everything (ZPA objects survive by design).

## Cost

Verified against the AWS Pricing API, eu-central-1, always-on:

| | $/month |
|---|---|
| PSE `m5.large` | 83.95 |
| App Connector ×2 `t3.medium` | 70.08 |
| MCU client `t3.medium` Windows | 48.47 |
| NAT gateway | 37.96 |
| EBS gp3 298 GiB | 28.37 |
| nginx `t3.micro` | 8.76 |
| Public IPv4 ×2 | 7.30 |
| **Total** | **~285** + NAT data processing |

## Safety notes

The ZPA scripts here are **create-only** by design — no `PUT`, no `DELETE` — with
one exception (`zpa_fix_se_group.py`) that is hard-guarded to a single group ID
and refuses to run against anything else. `zpa_snapshot.py` takes a SHA-hashed
baseline of the tenant before any write, and `zpa_verify.py` proves afterwards
that no pre-existing object was added, removed or modified.

Snapshot output is git-ignored: it contains live tenant topology.

## Licence

MIT
