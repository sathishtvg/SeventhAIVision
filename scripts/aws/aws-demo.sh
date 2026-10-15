#!/usr/bin/env bash
# The Seventh AI Vision demo server on AWS, in Singapore, started for a demo and stopped after.
#
# Two kinds of machine (SEVENTH_MODE):
#   free  (default)  m7i-flex.large, 2 vCPU, 8 GB, no GPU: eligible for AWS's free plan. Everything except the
#                    AI analytics: console, rosters, attendance, visitors, fleet, alerts, cameras' live view.
#   gpu              g4dn.xlarge, NVIDIA T4: everything including AI. Not free; may need the paid plan.
#
# Run from the repository root in Git Bash, after the account's owner has run `aws configure`:
#
#   scripts/aws/aws-demo.sh create --yes   once: login key, firewall, the machine (it starts running)
#   scripts/aws/aws-demo.sh setup          once, and again to update: installs, builds and starts everything
#   scripts/aws/aws-demo.sh up             before a demo: start, open the firewall to this laptop, print the address
#   scripts/aws/aws-demo.sh down           after a demo: stop (the disk is kept, and still counted)
#   scripts/aws/aws-demo.sh today          on the morning of a demo: today's shifts, visits, incidents
#   scripts/aws/aws-demo.sh live           five minutes before: an incident to dispatch, a situation to decide
#   scripts/aws/aws-demo.sh status | ssh | password
#   scripts/aws/aws-demo.sh destroy --yes  remove the machine and its disk for good
#
# Everything private stays on this laptop in ~/seventh-aws (outside the repository): the login key, and the
# demo accounts' password that `setup` fetches. The application's own secrets are made on the server and stay
# there. The firewall lets in only this laptop's current internet address (SSH 22, web 443, API 8000); `up`
# moves it to wherever the laptop is now.
#
# Other settings (environment): AWS_REGION (ap-southeast-1), SEVENTH_INSTANCE_TYPE, SEVENTH_DISK_GB,
# SEVENTH_AI_WORKERS (gpu only; default ai-worker-intrusion ai-worker-face), SEVENTH_BRANCH (main).
set -euo pipefail
# Git Bash would rewrite arguments that look like paths (/dev/sda1, ubuntu@host:/tmp/...). Windows paths are
# handed over explicitly instead (winpath).
export MSYS_NO_PATHCONV=1

MODE="${SEVENTH_MODE:-free}"
REGION="${AWS_REGION:-ap-southeast-1}"
case "$MODE" in
  free)
    TYPE="${SEVENTH_INSTANCE_TYPE:-m7i-flex.large}"
    DISK_GB="${SEVENTH_DISK_GB:-40}"
    AMI_OWNER=099720109477   # Canonical
    AMI_NAME="ubuntu/images/hvm-ssd*/ubuntu-jammy-22.04-amd64-server-*"
    ;;
  gpu)
    TYPE="${SEVENTH_INSTANCE_TYPE:-g4dn.xlarge}"
    DISK_GB="${SEVENTH_DISK_GB:-100}"
    AMI_OWNER=amazon         # NVIDIA driver, Docker and the NVIDIA Container Toolkit come with it
    AMI_NAME="Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*"
    ;;
  *) printf 'aws-demo: SEVENTH_MODE is free or gpu\n' >&2; exit 2 ;;
esac
NAME="seventh-demo"
LOCAL_DIR="$HOME/seventh-aws"
KEY_FILE="$LOCAL_DIR/$NAME.pem"
KNOWN_HOSTS="$LOCAL_DIR/known_hosts"
HERE="$(cd "$(dirname "$0")" && pwd)"

if command -v aws >/dev/null 2>&1; then AWS=aws; else AWS="/c/Program Files/Amazon/AWSCLIV2/aws.exe"; fi
if [ -x /c/Windows/System32/OpenSSH/ssh.exe ]; then SSH=/c/Windows/System32/OpenSSH/ssh.exe; else SSH=ssh; fi
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

# The Windows AWS tool may end its lines with a carriage return; nothing here wants one.
aws_() { "$AWS" --region "$REGION" --output text "$@" | tr -d '\r'; }
say() { printf '%s\n' "$*"; }
die() { printf 'aws-demo: %s\n' "$*" >&2; exit 1; }

instance_id() {
  aws_ ec2 describe-instances \
    --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].InstanceId' | awk '{print $1}'
}
need_instance() { ID="$(instance_id)"; [ -n "$ID" ] || die "no $NAME machine; run: scripts/aws/aws-demo.sh create --yes"; }
address() { aws_ ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].PublicIpAddress'; }
sg_id() {
  aws_ ec2 describe-security-groups --filters "Name=group-name,Values=$NAME" --query 'SecurityGroups[0].GroupId' \
    | sed 's/^None$//'
}

ssh_() {
  local ip="$1"; shift
  "$SSH" -i "$(winpath "$KEY_FILE")" -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$(winpath "$KNOWN_HOSTS")" \
    -o ConnectTimeout=15 "ubuntu@$ip" "$@"
}

allow_this_laptop() {
  local sg="$1" me rules
  me="$(curl -fsS https://checkip.amazonaws.com | tr -d '\r\n ')"
  [ -n "$me" ] || die "could not find this laptop's internet address"
  rules="$("$AWS" --region "$REGION" --output json ec2 describe-security-groups --group-ids "$sg" \
           --query 'SecurityGroups[0].IpPermissions' | tr -d '\r')"
  if [ "$(printf '%s' "$rules" | tr -d ' \n')" != "[]" ]; then
    "$AWS" --region "$REGION" ec2 revoke-security-group-ingress --group-id "$sg" --ip-permissions "$rules" >/dev/null
  fi
  for port in 22 443 8000; do
    aws_ ec2 authorize-security-group-ingress --group-id "$sg" --protocol tcp --port "$port" --cidr "$me/32" >/dev/null
  done
  say "Firewall: ports 22, 443 and 8000 open to this laptop only ($me)."
}

wait_for_ssh() {
  local ip="$1" i
  for i in $(seq 1 40); do
    if ssh_ "$ip" true 2>/dev/null; then return 0; fi
    sleep 6
  done
  die "the machine is running but does not answer SSH at $ip"
}

cmd_create() {
  [ "${1:-}" = "--yes" ] || die "create starts a $TYPE machine with a ${DISK_GB} GB disk; run it with --yes"
  [ -z "$(instance_id)" ] || die "a $NAME machine exists already (scripts/aws/aws-demo.sh status)"
  say "Account: $(aws_ sts get-caller-identity --query Account)   Region: $REGION   Mode: $MODE ($TYPE)"
  mkdir -p "$LOCAL_DIR"
  if [ ! -f "$KEY_FILE" ]; then
    aws_ ec2 describe-key-pairs --key-names "$NAME" >/dev/null 2>&1 \
      && die "AWS has a key pair named $NAME but this laptop has no $KEY_FILE; delete it in the console first"
    aws_ ec2 create-key-pair --key-name "$NAME" --key-type ed25519 --query KeyMaterial > "$KEY_FILE"
    # Windows' ssh refuses a key others can read: only the owner may read it.
    if command -v icacls >/dev/null 2>&1; then
      icacls "$(cygpath -w "$KEY_FILE")" /inheritance:r /grant:r "${USERNAME:-$USER}:R" >/dev/null
    else
      chmod 600 "$KEY_FILE"
    fi
    say "Login key saved to $KEY_FILE (keep it: it is the only way in)."
  fi
  local sg vpc ami root
  sg="$(sg_id)"
  if [ -z "$sg" ]; then
    vpc="$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId')"
    [ -n "$vpc" ] && [ "$vpc" != "None" ] || die "this region has no default network (VPC)"
    sg="$(aws_ ec2 create-security-group --group-name "$NAME" --vpc-id "$vpc" \
          --description "Seventh AI Vision demo: this laptop only" --query GroupId)"
  fi
  allow_this_laptop "$sg"
  ami="$(aws_ ec2 describe-images --owners "$AMI_OWNER" \
         --filters "Name=name,Values=$AMI_NAME" "Name=state,Values=available" "Name=architecture,Values=x86_64" \
         --query 'sort_by(Images,&CreationDate)[-1].ImageId')"
  [ -n "$ami" ] && [ "$ami" != "None" ] || die "could not find the Ubuntu 22.04 image for $MODE in $REGION"
  root="$(aws_ ec2 describe-images --image-ids "$ami" --query 'Images[0].RootDeviceName')"
  say "Starting $TYPE from $ami ..."
  ID="$(aws_ ec2 run-instances --image-id "$ami" --instance-type "$TYPE" --key-name "$NAME" \
        --security-group-ids "$sg" --metadata-options HttpTokens=required \
        --block-device-mappings "DeviceName=$root,Ebs={VolumeSize=$DISK_GB,VolumeType=gp3,DeleteOnTermination=true}" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=seventh-ai-vision},{Key=Mode,Value=$MODE}]" \
                             "ResourceType=volume,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=seventh-ai-vision}]" \
        --query 'Instances[0].InstanceId')"
  aws_ ec2 wait instance-running --instance-ids "$ID"
  local ip; ip="$(address "$ID")"
  wait_for_ssh "$ip"
  say "Running: $ID at $ip. Next: scripts/aws/aws-demo.sh setup   (and 'down' when you are done)"
}

cmd_setup() {
  need_instance
  [ "$(aws_ ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].State.Name')" = running ] \
    || die "the machine is not running; run: scripts/aws/aws-demo.sh up"
  local ip mode; ip="$(address "$ID")"
  mode="$(aws_ ec2 describe-instances --instance-ids "$ID" --query "Reservations[0].Instances[0].Tags[?Key=='Mode'].Value | [0]")"
  [ -n "$mode" ] && [ "$mode" != "None" ] || mode="$MODE"
  # Sent through SSH without carriage returns: a Windows checkout may have them, and bash on the server would not run it.
  tr -d '\r' < "$HERE/server-setup.sh" | ssh_ "$ip" "cat > /tmp/server-setup.sh"
  ssh_ "$ip" "SEVENTH_MODE='$mode' SEVENTH_AI_WORKERS='${SEVENTH_AI_WORKERS:-}' SEVENTH_BRANCH='${SEVENTH_BRANCH:-main}' bash /tmp/server-setup.sh"
  cmd_password quiet
  say "Open https://$ip  (the certificate is self-signed: accept the warning once)."
}

cmd_story() {  # today | live: the demo story's fresh parts, with this server's password
  need_instance
  local ip; ip="$(address "$ID")"
  tr -d '\r' < "$HERE/server-setup.sh" | ssh_ "$ip" "cat > /tmp/server-setup.sh"
  ssh_ "$ip" "SEVENTH_DEMO_STEP='$1' bash /tmp/server-setup.sh"
}

cmd_password() {
  need_instance
  local ip; ip="$(address "$ID")"
  mkdir -p "$LOCAL_DIR"
  ssh_ "$ip" "sudo cat /opt/seventh/demo-accounts.txt" > "$LOCAL_DIR/demo-accounts.txt"
  say "Demo accounts and their password: $LOCAL_DIR/demo-accounts.txt"
  [ "${1:-}" = quiet ] || say "(the file is on this laptop only; it is not printed here)"
}

cmd_up() {
  need_instance
  aws_ ec2 start-instances --instance-ids "$ID" >/dev/null
  aws_ ec2 wait instance-running --instance-ids "$ID"
  allow_this_laptop "$(sg_id)"
  local ip i code=""; ip="$(address "$ID")"
  say "Running at $ip. Waiting for the application ..."
  for i in $(seq 1 60); do
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "https://$ip/" || true)"
    [ "$code" = 200 ] && break
    sleep 5
  done
  [ "$code" = 200 ] && say "Ready: https://$ip" || say "Not answering yet at https://$ip (still starting, or run setup)"
}

cmd_down() {
  need_instance
  aws_ ec2 stop-instances --instance-ids "$ID" >/dev/null
  aws_ ec2 wait instance-stopped --instance-ids "$ID"
  say "Stopped. The machine costs nothing now; the ${DISK_GB} GB disk is still counted until 'destroy'."
}

cmd_status() {
  local id; id="$(instance_id)"
  [ -n "$id" ] || { say "No $NAME machine in $REGION."; return; }
  aws_ ec2 describe-instances --instance-ids "$id" \
    --query 'Reservations[0].Instances[0].[InstanceId,InstanceType,State.Name,PublicIpAddress,LaunchTime]'
}

cmd_ssh() { need_instance; ssh_ "$(address "$ID")"; }

cmd_destroy() {
  [ "${1:-}" = "--yes" ] || die "destroy deletes the machine, its disk and the demo data for good; run it with --yes"
  need_instance
  aws_ ec2 terminate-instances --instance-ids "$ID" >/dev/null
  aws_ ec2 wait instance-terminated --instance-ids "$ID"
  say "Terminated $ID and its disk. The firewall group and login key are kept (free)."
}

case "${1:-}" in
  create) shift; cmd_create "$@" ;;
  setup) cmd_setup ;;
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  ssh) cmd_ssh ;;
  password) cmd_password ;;
  today) cmd_story today ;;
  live) cmd_story live ;;
  destroy) shift; cmd_destroy "$@" ;;
  *) sed -n '2,28p' "$0"; exit 2 ;;
esac
