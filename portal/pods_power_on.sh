#!/bin/bash
# Install (start) the fabric on every pod over the FS CLI, because nested VMs
# don't reliably autostart on the clones.
#
#   export FS_PASS='<FS admin password>'
#   pods-power-on <fabric-id> [s01 s02 ...]           install
#   ACTION=uninstall pods-power-on <fabric-id> [...]  uninstall
#
# <fabric-id> is the fabric's ID in Fabric Studio (FS CLI: runtime fabric list).
# "OK" only means FS queued the task; watch Tasks in the pod's FS GUI.
set -u
CFG=/opt/adc-portal/config.json
FABRIC="${1:?usage: pods-power-on <fabric-id> [pods...]}"; shift
ACTION="${ACTION:-install}"
: "${FS_PASS:?export FS_PASS='<FS admin password>' first}"
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
