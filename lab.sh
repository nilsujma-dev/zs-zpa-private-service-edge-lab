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
# provisioning key (maxUsage 5) and leaves a stale entry in ZPA -- see
# ./lab.sh prune-help.
#
# Requires AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN in env.

set -euo pipefail
S="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF="$S/tf"
REG="eu-central-1"
export PATH="/opt/homebrew/bin:$PATH"

need_creds() {
  : "${AWS_ACCESS_KEY_ID:?export your AWS credentials first}"
  : "${AWS_SECRET_ACCESS_KEY:?export your AWS credentials first}"
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
    python3 "$S/status.py" 2>/dev/null | sed -n '/ZPA/,$p'
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
    cd "$TF"
    tofu plan -out=lab.tfplan
    echo
    read -r -p "apply this plan? [y/N] " a
    [ "$a" = "y" ] || { echo "aborted"; exit 1; }
    tofu apply lab.tfplan
    ;;

  down)
    need_creds
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
    python3 "$S/put_keys_ssm.py"
    ;;

  prune-help)
    cat <<'TXT'
ENROLMENT IS FULLY UNATTENDED. No codes, no console, no login.

Each VM pulls its provisioning key from SSM Parameter Store at boot and
self-enrols. The bootstrap mirrors Zscaler's own user_data_rhel9.sh
provisioning_key branch -- three details matter and are easy to get wrong:

  * /opt/zscaler/var/provision_key must be mode 644, not 600. The service
    does not run as root and cannot read a 600 file.
  * The key is written with 'echo', so it carries a trailing newline.
  * The service must be stopped and started a SECOND time after ~60s. The
    image starts in OAuth mode at boot, before cloud-init runs, so a single
    start does not switch it to key-based enrolment.

If a rebuild ever falls back to printing an OAuth code on the console, one of
those three regressed.

AFTER A 'down', TWO THINGS TO KNOW:

  1. Each provisioning key has maxUsage 5. Five rebuilds exhausts it and the
     sixth silently falls back to OAuth. Raise the limit in the ZPA console
     (Infrastructure > Provisioning Keys) before running long automation.
     Check current usage with:  ./lab.sh status

  2. ZPA keeps the old enrolled entries; they show as disconnected and pile up
     one per rebuild. Removing them is a DELETE against ZPA, and the credential
     in use may be broadly scoped, so that is deliberately NOT scripted.
     Do it in the console, where the blast radius is visible. Delete only under:
       AWS-Lab PSE Group / AWS-Lab App Connector Group / AWS-Lab PRIV Connector Group
TXT
    ;;

  *) echo "usage: $0 {status|stop|start|up|down|keys|prune-help}"; exit 1 ;;
esac
