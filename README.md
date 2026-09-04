# zs-zpa-private-service-edge-lab

A reproducible AWS lab for a Zscaler Private Access **Private Service Edge**: two
isolated VPCs, three network zones, and unattended provisioning-key enrolment.

## Status

| | |
|---|---|
| **Owner** | Nils Ujma |
| **Context** | Zscaler — ZPA / IoT-OT EBC demo lab |
| **Stability** | experimental |
| **Runtime** | OpenTofu 1.12+, Python 3.11+ (stdlib only), AWS CLI v2 |

## Why

The IoT/OT EBC needs a Private Service Edge it can stand up, break, and rebuild
without touching the production tenant's topology. This builds one end to end in
AWS, and — more usefully — records the four failure modes that make PSE
automation silently fall back to interactive OAuth enrolment.

There is **no vendor licensing cost**. The ZPA AMIs carry no marketplace product
code and bill as plain `RunInstances`, so the only spend is EC2 infrastructure.

## Topology

```
                         internet / Zscaler cloud
                                   |
        ┌──────────────────────────┼───────────────────────────┐
        │  VPC A · isolated        │        VPC B · private     │
        │  10.91.0.0/16            │        10.90.0.0/16        │
        │                          │                            │
        │  ┌────────────────┐      │   ┌──────────────────┐     │
        │  │ PSE            │◄─────┼───│ NAT gateway      │     │
        │  │ elastic IP     │      │   └────────┬─────────┘     │
        │  │ App Connector  │      │            │               │
        │  └────────────────┘      │   ┌────────┴─────────┐     │
        │                          │   │ PRIV  10.90.20/24│     │
        │                          │   │ connector + nginx│     │
        │                          │   └────────╳─────────┘     │
        │                          │        no direct path      │
        │                          │   ┌────────┴─────────┐     │
        │                          │   │ MCU   10.90.30/24│     │
        │                          │   │ Windows client   │     │
        │                          │   └──────────────────┘     │
        └──────────────────────────┴────────────────────────────┘
```

**VPC A** is genuinely isolated — no peering, no transit gateway, no shared route
table. The two VPCs could sit in different accounts or regions unchanged.

**VPC B** holds two zones that cannot reach each other. The MCU client reaches the
PRIV server *only* through ZPA brokering; a direct connection fails. That block is
enforced twice — by security-group omission and by a subnet network ACL — so it
survives a loosened security group.

With no peering, security groups cannot reference each other across VPCs, so the
PSE accepts `443` from exactly one address: VPC B's NAT elastic IP. Tighter than a
peered rule, not looser.

## Quick start

```sh
git clone git@github.com:nilsujma-dev/zs-zpa-private-service-edge-lab.git
cd zs-zpa-private-service-edge-lab

python3 scripts/zpa_create.py        # service edge group, connector group, keys
python3 scripts/zpa_create_priv.py   # PRIV connector group + key
python3 scripts/put_keys_ssm.py      # keys -> SSM SecureString (never to disk)
./lab.sh up                          # plan, review, apply
./lab.sh status                      # instances + ZPA enrolment
```

`./lab.sh stop` parks the VMs (~$74/mo — NAT, elastic IPs and volumes persist).
`./lab.sh down` destroys all AWS resources; ZPA objects survive by design.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `AWS_ACCESS_KEY_ID` | yes | — | AWS credentials; region and account are resolved at plan time |
| `AWS_SECRET_ACCESS_KEY` | yes | — | |
| `AWS_SESSION_TOKEN` | if SSO | — | |
| `ZS_ISSUER` | yes | — | `https://<tenant>.zslogin.net` |
| `ZS_CLIENT_ID` | yes | — | ZIdentity OneAPI client id |
| `ZPA_CUSTOMER_ID` | yes | — | ZPA customer id from the token's `service-info` |
| `~/.zscaler_api_key` | yes | — | OneAPI client secret, read from file — never an argument |

Secrets never live in this repo. Provisioning keys are held only in **SSM
Parameter Store**: never on disk, never in `user_data`, never in OpenTofu state.
Each instance reads its own parameter at boot through its IAM role.

## Lab / network notes

| | |
|---|---|
| Region | `eu-central-1` (nearest to the Amsterdam-anchored ZPA groups) |
| VPC A | `10.91.0.0/16` — PSE + co-located App Connector, public subnet |
| VPC B | `10.90.0.0/16` — `10.90.0.0/24` NAT, `10.90.20.0/24` PRIV, `10.90.30.0/24` MCU |
| Reachability | PSE via elastic IP; everything else egresses through one NAT address |
| Instance types | Service Edge accepts only 8 of 28 types tested — see `tools/type_sweep.py` |

The Service Edge rejects every `t3.*` size while the App Connector accepts them.
Supported for the edge: `t3a.large`, `m5.large`–`m5.4xlarge`, `m6i.large`,
`m6i.xlarge`, `m6a.large`.

## Runbook

See [docs/runbook.md](docs/runbook.md) for enrolment failure modes, diagnostics on
an instance with no SSH and no SSM agent, and the ZPA safety model.

## Repo checklist

- [x] Name follows `zs-<area>-<thing>`
- [x] Topics set: `zscaler`, `zpa`, `terraform`, `ot-security`, `aws`
- [x] Description filled in on GitHub
- [x] `.gitignore` covers local config, state and tenant snapshots
- [x] Secrets confirmed absent from history — see `docs/runbook.md`

## Licence

MIT — see [LICENSE](LICENSE).
