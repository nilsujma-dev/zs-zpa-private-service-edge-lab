#!/usr/bin/env bash
# ZPA lab lifecycle.
#
#   ./lab.sh status   what exists and what has enrolled
#   ./lab.sh stop     shut the 5 VMs down, keep everything else  (~$45/mo idle)
#   ./lab.sh start    boot them again, enrolment survives
#   ./lab.sh up       build from nothing (plan, show, then apply)
#   ./lab.sh down     destroy all AWS resources (ZPA objects survive)
#   ./lab.sh keys     re-seed the provisioning keys into SSM (safe to re-run)
#
# Enrolment is unattended: each VM pulls its provisioning key from SSM at boot.
# No OAuth code, no console login. See './lab.sh prune-help' for the details that
# make that work.
#
# stop/start is the cheap day-to-day cycle: instances keep their identity, the
# PSE keeps its elastic IP, and nothing has to re-enrol.
# up/down is the full teardown. Each rebuild consumes one use of each
# provisioning key (v2 keys are created with maxUsage 25) and leaves a stale
# entry in ZPA -- see ./lab.sh prune-help.
#
# Requires AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN in env.

set -euo pipefail
S="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF="$S/terraform"
REG="eu-central-1"
export PATH="/opt/homebrew/bin:$PATH"

need_creds() {
  : "${AWS_ACCESS_KEY_ID:?export your AWS credentials first}"
  : "${AWS_SECRET_ACCESS_KEY:?export your AWS credentials first}"
}

tf_init() {
  # Remote state lives in S3 (see terraform/backend.tf). Same command Switchboard uses,
  # so a laptop and the control plane share one state and one lock.
  local acct; acct=$(aws sts get-caller-identity --query Account --output text)
  tofu -chdir="$TF" init -input=false -reconfigure \
    -backend-config="bucket=zs-lab-tfstate-${acct}" \
    -backend-config="key=usecases/zpa-private-service-edge/terraform.tfstate" \
    -backend-config="region=${REG}" \
    -backend-config="use_lockfile=true" >/dev/null
}

ids() {
  aws ec2 describe-instances --region "$REG" \
    --filters "Name=tag:Project,Values=zpa-pse-lab" \
              "Name=instance-state-name,Values=running,stopped,stopping,pending" \
    --query 'Reservations[].Instances[].InstanceId' --output text
}

case "${1:-status}" in
  status)
    need_creds
    echo "=== instances ==="
    aws ec2 describe-instances --region "$REG" \
      --filters "Name=tag:Project,Values=zpa-pse-lab" \
      --query 'Reservations[].Instances[].[Tags[?Key==`Name`]|[0].Value,InstanceId,InstanceType,State.Name,PrivateIpAddress]' \
      --output table
    echo "=== ZPA enrolment ==="
    python3 "$S/scripts/status.py" 2>/dev/null
    ;;

  stop)
    need_creds
    I=$(ids); [ -z "$I" ] && { echo "nothing to stop"; exit 0; }
    echo "stopping: $I"
    aws ec2 stop-instances --region "$REG" --instance-ids $I \
      --query 'StoppingInstances[].[InstanceId,CurrentState.Name]' --output text
    echo "NAT gateway and elastic IPs keep running (~\$45/mo). Use 'down' to remove those too."
    ;;

  start)
    need_creds
    I=$(ids); [ -z "$I" ] && { echo "nothing to start -- run './lab.sh up'"; exit 0; }
    echo "starting: $I"
    aws ec2 start-instances --region "$REG" --instance-ids $I \
      --query 'StartingInstances[].[InstanceId,CurrentState.Name]' --output text
    echo "give the connectors 2-3 minutes to re-establish their tunnels."
    ;;

  up)
    need_creds
    tf_init
    cd "$TF"
    tofu plan -out=lab.tfplan
    echo
    read -r -p "apply this plan? [y/N] " a
    [ "$a" = "y" ] || { echo "aborted"; exit 1; }
    tofu apply lab.tfplan
    ;;

  down)
    need_creds
    tf_init
    cd "$TF"
    echo "This destroys every AWS resource in the lab."
    echo "ZPA groups and provisioning keys are NOT touched -- they live outside tofu."
    tofu plan -destroy -out=destroy.tfplan
    echo
    read -r -p "type 'destroy' to confirm: " a
    [ "$a" = "destroy" ] || { echo "aborted"; exit 1; }
    tofu apply destroy.tfplan
    echo
    echo "Done. The enrolled Service Edge and App Connectors will now show as"
    echo "disconnected in ZPA. See './lab.sh prune-help' before rebuilding."
    ;;

  keys)
    # Re-seed the three provisioning keys into SSM. Safe to re-run; values are
    # fetched from ZPA and written straight to SSM, never to disk.
    need_creds
    python3 "$S/scripts/put_keys_ssm.py"
    ;;

  prune-help)
    cat <<'TXT'
ENROLMENT IS FULLY UNATTENDED. No codes, no console, no login.

Each VM pulls its provisioning key from SSM Parameter Store at boot and
self-enrols. The bootstrap mirrors Zscaler's own user_data_zscaler.sh (the
Marketplace-AMI variant). The details that matter -- and the one that costs the
most time, the Service Edge reading its key from a DIFFERENT path than the App
Connector -- are in docs/runbook.md, "five silent failure modes".

If a rebuild ever falls back to printing an OAuth code on the console, start
with failure mode 0 in the runbook (the key path).

AFTER A 'down', TWO THINGS TO KNOW:

  1. scripts/zpa_create.py creates v2 provisioning keys with maxUsage 25. If
     you are still on the original v1 keys (maxUsage 5), the sixth rebuild
     silently falls back to OAuth -- re-run zpa_create.py and keys to move.

  2. ZPA keeps the old enrolled entries; they show as disconnected and pile up
     one per rebuild. Removing them is a DELETE against ZPA, and the credential
     in use may be broadly scoped, so that is deliberately NOT scripted.
     Do it in the console, where the blast radius is visible. Delete only under:
       AWS-Lab PSE Group / AWS-Lab App Connector Group / AWS-Lab PRIV Connector Group
TXT
    ;;

  *) echo "usage: $0 {status|stop|start|up|down|keys|prune-help}"; exit 1 ;;
esac
