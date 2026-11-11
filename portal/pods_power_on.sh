#!/bin/bash
# Install (start) the fabric on every pod over the FS CLI, because nested VMs
# don't reliably autostart on the clones.
#
#   (password: fs_admin_password from Terraform, or export FS_PASS='<FS admin password>')
#   pods-power-on [fabric-id] [s01 s02 ...]           install (fabric ID defaults to 1)
#   ACTION=uninstall pods-power-on [fabric-id] [...]  uninstall
#
# The ADC image has one fabric, ID 1 ("FortiADC Global App Delivery"; FS CLI: runtime fabric detail).
# "OK" only means FS queued the task; watch Tasks in the pod's FS GUI.
set -u
CFG="${ADC_PORTAL_DIR:-/opt/adc-portal}/config.json"
FABRIC="${1:-1}"; [ $# -gt 0 ] && shift
ACTION="${ACTION:-install}"
if [ -z "${FS_PASS:-}" ] && [ -r "$CFG" ]; then
  FS_PASS=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("fs_password") or "")' "$CFG")
fi
: "${FS_PASS:?set fs_admin_password in terraform.tfvars, or export FS_PASS='<FS admin password>'}"
command -v sshpass >/dev/null || { echo "sudo apt-get install -y sshpass" >&2; exit 1; }

if [ $# -gt 0 ]; then FILTER=" $* "; else FILTER=""; fi

python3 -c 'import json,sys; [print(p["id"], p["ip"]) for p in json.load(open(sys.argv[1]))["pods"]]' "$CFG" |
while read -r pod ip; do
  if [ -n "$FILTER" ] && [[ "$FILTER" != *" $pod "* ]]; then continue; fi
  if out=$(SSHPASS="$FS_PASS" sshpass -e ssh -n -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 -o LogLevel=ERROR "admin@$ip" "runtime fabric $ACTION $FABRIC" 2>&1); then
    echo "$pod $ip OK   $(echo "$out" | tail -1)"
  else
    echo "$pod $ip FAIL $(echo "$out" | tail -1)"
  fi
done
