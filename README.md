# adc-oci — FortiADC workshop on OCI

Runs the **FortiADC Global App Delivery** workshop (SLB, GLB, LLB) in Oracle Cloud,
one Fabric Studio pod per student, behind a single front FortiGate.

```
Students (venue CIDR) ─┐
Instructor (admin CIDR)┴─> DDNS name ─> front FortiGate (one-arm, port1)
                                         │  VIP block per student (20 ports)
                                         ├─> pod s01 10.20.1.101  (Fabric Studio: 2x FAD, FGT, clients, DNS, web servers)
                                         ├─> pod sNN 10.20.1.(100+N)
                                         └─> portal 10.20.1.10  (check-in, ports, logins, lab guide)
 licence server 10.20.3.10 ──licenses the FortiADCs in every pod (FortiFlex pool)
```

No grader, no scoreboard. Two Terraform stacks:

| Stack | Contents | Lifetime |
| --- | --- | --- |
| `base/` | VCN `10.20.0.0/16`, internet gateway, mgmt subnet, **FS licence server**, optional **FS builder** | Kept; licence server stopped between classes |
| `class/` | public + inside subnets, **front FortiGate**, **N pods**, **portal VM**, a private bucket for the portal files | `apply` before the class, `destroy` after |

Run everything from **OCI Cloud Shell** (Terraform and your OCI login are already there).
`class/` reads `../base/terraform.tfstate`, so keep both stacks in the same checkout.

---

## 1. Base stack (once)

```bash
cd base
cp terraform.tfvars.example terraform.tfvars    # compartment, admin_cidrs, fs_image_ocid
terraform init
terraform apply
terraform output                                # licence server + builder URLs
```

`fs_image_ocid` is a Fabric Studio cloud image (the official one in the tenancy is fine).
Fabric Studio on `VM.Standard.E5.Flex` needs Secure Boot off (the default here); first boot takes
several minutes after OCI says *Running*.

### Licence server
1. Log in at `license_server_gui`, register it to FNDN (after an OCI stop/start it can come back
   unregistered: re-register before a class).
2. Before each class build, generate a fresh FortiFlex pool for the FortiADCs on its FS CLI:
   ```
   system license flex pool generate FAD <count>
   ```
   Size it at **FortiADCs per pod × pods + a few spare** (the workshop has 2 FADs per pod; the demo's
   HA configs need 4). New pod VMs never reuse old licences; a fabric reinstall on the same pod does.

### Builder → class image
1. `builder_enabled = true` in `base/terraform.tfvars`, `terraform apply`, open `builder_gui`.
2. Import the exported FortiADC instance into Fabric Studio, point FS at the licence server
   (`http://10.20.3.10`), install the fabric and check that the FortiADCs license.
3. Make the device **HTTPS accesses PUBLIC**. FS publishes them through its reverse proxy on 13xxx, and by
   default (PRIVATE) they only answer a browser logged in to that FS GUI, so through the FortiGate they 404.
   On the builder's FS CLI:
   ```
   system security preferences set '{"https_public_allowed": "yes"}'
   system security preferences set '{"web_session_age": 28800}'     # guest/admin web session 8 h (default 1 h)
   model vm access list                                            # find the HTTPS access IDs
   model vm access update <ID> '{"mode": "PUBLIC"}'                # FAD-Cannes 13002, FAD-Sunnyvale 13004, FGT 13006
   ```
   "Public" only means no FS login on that port; only the front FortiGate reaches it (venue + admin CIDRs).
   The defaults in `services` use them:

   | Suffix | Pod port | Target | Portal shows |
   | --- | --- | --- | --- |
   | 00 | 443 | FS GUI | "Lab console" link + guest login |
   | 01 | 13002 | FAD-Cannes HTTPS, path `/ui/` | FAD-Cannes GUI |
   | 03 | 13004 | FAD-Sunnyvale HTTPS, path `/ui/` | FAD-Sunnyvale GUI |
   | 05 | 13006 | FGT-internet-router HTTPS | Internet FortiGate GUI |

   Student N's external port = 20000 + (N−1)×20 + suffix (s01: 20001…, s02: 20021…).
   Need more devices (DNS servers, web servers)? Add `services` entries (suffix < 20) and/or `web_terminals` lines.
   FS access links built into the GUI don't survive the port translation; redirections do.
   SSH / console access: students log in to their pod's FS GUI as the FS **guest** account (topology, guide,
   SERIAL / SSH / VNC buttons, no admin rights). Those buttons ride on the FS GUI's 443 and work through the
   front FortiGate; the HTTP/HTTPS buttons don't (they open `<host>:13xxx`, the same on every pod), so the
   portal links the device GUIs on the student's own ports instead. Set the guest password in the builder
   before capturing, and put it in `fs_guest_password`. The FS GUI port (suffix 00) is therefore open to the
   venue, not just the instructor.
4. **Stop** the builder in the OCI console, then
   *More actions → Create custom image*. Its OCID is `class_image_ocid`.
5. `builder_enabled = false`, `terraform apply` (removes the builder).

---

## 2. Class build (each class)

```bash
cd class
cp terraform.tfvars.example terraform.tfvars
```

Set at least: `student_count` (registered + spare), `admin_cidrs`, `student_source_cidrs` (the venue's
public IP), `class_image_ocid`, `fgt_listing_version`, `fgt_license_token`, `fgt_ddns_hostname`,
`portal_passphrase`, `ssh_public_key`. Optional: `roster_csv` (registration export; only Email /
First Name / Last Name are kept), `capacity_reservation_id`, `pod_ocpus` / `pod_memory_gb`.

```bash
terraform init
terraform plan -out=class.tfplan
terraform apply class.tfplan
terraform output
```

Notes
- **FortiFlex token for the front FortiGate is single use per VM**: a new token on the *same serial*
  every build keeps the FortiGuard DDNS name working. Without a token/DDNS students use the public IP.
- First FortiGate login: `fgt_admin_url`, user `admin`, password = `fgt_instance_id`. Admin access only
  from `admin_cidrs`.
- The bootstrap uses exactly **3 firewall policies** (`students-in`, `admin-in`, `inside-out`) because an
  unlicensed FortiGate-VM only accepts 3 and the config is applied before FortiFlex licenses it.
- Pods have no public IP. Their default route is the FortiGate, so VIP traffic isn't SNATed and FS sees
  the real client IP. Pod → licence server traffic stays inside the VCN.
- `out/access.csv` lists every pod's URLs (instructor copy).
- Change Terraform-managed resources in code, not in the console (console edits cause drift).

### Start the pods
Nested VMs don't reliably start on their own. From the portal VM:

```bash
ssh -p 2222 ubuntu@<ddns name>             # terraform output portal_ssh
export FS_PASS='<FS admin password>'       # single quotes
pods-power-on                              # runtime fabric install 1 on every pod; "OK" = queued
pods-check                                 # TCP check of every redirected port on every pod
```

Watch progress in a pod's FS GUI (`https://<ddns>:20000/` for s01, `:20020` for s02, …).
`ACTION=uninstall pods-power-on 1` reinstalls from scratch (licences come back on their own).

### Portal
`http://<ddns name>/` — class passphrase + email (+ name when there's no roster). A student gets the next
free pod, a page with their device links / SSH commands / logins, and the lab guide at `/guide/`.
Unregistered emails are told to see the instructor.

**Admin page** `http://<ddns name>:8005/` (`terraform output portal_admin_url`; log in on the page as `admin` +
`portal_admin_password`, reachable from `admin_cidrs` only, plain HTTP): live counts (pods in use / free,
registered checked in, walk-ins), the pod table with release / disable buttons and FS links, assign / move
a student, upload the registration CSV (merge or replace; only email + names are kept), add walk-ins, the
"registered but not checked in yet" list and a CSV export. It refreshes every 15 s.

**Pod health** on the same page: every minute the portal TCP-probes each pod's forwarded ports (FS GUI 443 and
the 13xxx device GUIs) and shows a pod × port grid, with **Check now**, **Power on all pods** and a per-pod
**power on** button next to any pod with ports down (`runtime fabric install <fabric_id>`, output shown on the
page). The power-on buttons need `fs_admin_password` in tfvars; `pods-power-on` on the VM uses it too.

**Licence pool** on the same page: reads `system license list` on the licence server (10.20.3.10, same FS admin
password) every 5 min or on **Refresh list**, shows each pool type (FAD, FGT) against pods × `license_per_pod`
(green = enough, red = short by N), the serial list, and a **Generate** form (type drop-down + count) that runs
`system license flex pool generate TYPE COUNT` and shows its output.

The same from the CLI on the portal VM (`sudo` because the state lives in `/var/lib/adc-portal`):

```bash
sudo adc-portal list                                 # who has which pod
sudo adc-portal roster import ~/Partners-Rego.csv    # then: rm ~/Partners-Rego.csv
sudo adc-portal roster add EMAIL FIRST LAST          # walk-in
sudo adc-portal assign jane@acme.com s05             # move someone
sudo adc-portal release s05                          # free a pod (POD or EMAIL)
sudo adc-portal disable s05 / enable s05             # keep a broken pod out of the pool
sudo adc-portal export > checkins.csv
curl -s localhost:8080/healthz
```

No roster = open check-in. `roster clear` goes back to open check-in. A reused pod keeps the previous
student's FortiADC config: give newcomers an unused pod.

The portal VM pulls its files at boot from the class bucket through a read-only link valid for
`par_valid_hours` (72 h). After changing the guide or portal settings in Terraform and applying, run
`sudo /opt/adc-portal/fetch.sh` on the portal within that window (or the VM is replaced).

### Admin access
`admin_cidrs` is enforced on the front FortiGate: `trusthost1..N` on the `admin` account (GUI 8443, SSH 22) and
policy `admin-in` (source group `src-admin`) for the portal SSH (2222) and admin page (8005) VIPs. With
`admin_ports_open = true` (default) the OCI security list leaves those ports open, so to add an admin IP during a
class just change the FortiGate: add a trusthost on `admin` and an address to `src-admin`.

### Let another source IP in during the class (no Terraform)
OCI console → VCN `adclab-vcn` → security list `adclab-sl-public` → add ingress for `<ip>/32`, TCP 80 and
the student port range; then on the FortiGate add an address to the `src-students` group. A later
`terraform apply` removes the hand-made security rule.

---

## 3. After the class

1. Licence server FS CLI: `system license flex license stop all --force`.
2. `cd class && terraform destroy`.
3. Stop the front FortiGate's FortiFlex token(s) by serial; stop the licence server instance; delete any
   capacity reservation (billed whether used or not).

---

## Layout

```
base/      VCN, licence server, builder
class/     subnets, FortiGate (+ fgt-bootstrap.tftpl), pods, portal VM (+ portal-cloudinit.tftpl)
portal/    adc_portal.py (web + CLI), pods_check.py, pods_power_on.sh — Python stdlib, no installs
guide/     FortiADC workshop guide (static HTML, served by the portal)
```
