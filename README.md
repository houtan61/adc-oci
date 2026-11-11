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
3. Create the **FS port redirections** the front FortiGate forwards to (pod port → device:port).
   They must match `services` in `class/variables.tf`; the defaults are:

   | Suffix | Pod port | Target | Shown to students as |
   | --- | --- | --- | --- |
   | 00 | 443 | FS GUI itself (no redirection) | not listed; carries the browser terminals (`web_terminals`) |
   | 01 | 10001 | FAD-Cannes port1 :443 | FAD-Cannes GUI |
   | 02 | 10002 | FAD-Cannes port1 :22 | FAD-Cannes SSH |
   | 03 | 10003 | FAD-Sunnyvale port1 :443 | FAD-Sunnyvale GUI |
   | 04 | 10004 | FAD-Sunnyvale port1 :22 | FAD-Sunnyvale SSH |
   | 05 | 10005 | Internet FortiGate :443 | FortiGate GUI |
   | 06 | 10006 | Internet FortiGate :22 | FortiGate SSH |
   | 07 | 10007 | zClient :22 | zClient SSH |

   Student N's external port = 20000 + (N−1)×20 + suffix (s01: 20001…, s02: 20021…).
   Need more devices (DNS servers, web servers)? Add redirections and `services` entries (suffix < 20).
   FS access links built into the GUI don't survive the port translation; redirections do.
   SSH/shell access uses FS's **browser terminals** instead (ttyd, e.g. `/ttyd/ssh/FADCa1/` on the FS GUI),
   as the guide intends. List them in `web_terminals` in `class/terraform.tfvars`; the portal links them
   on the pod's FS GUI port. Because of that the FS GUI port (suffix 00) is open to the venue, not just
   the instructor.
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
pods-power-on <fabric-id>                  # runtime fabric install <id> on every pod; "OK" = queued
pods-check                                 # TCP check of every redirected port on every pod
```

Watch progress in a pod's FS GUI (`https://<ddns>:20000/` for s01, `:20020` for s02, …).
`ACTION=uninstall pods-power-on <fabric-id>` reinstalls from scratch (licences come back on their own).

### Portal
`http://<ddns name>/` — class passphrase + email (+ name when there's no roster). A student gets the next
free pod, a page with their device links / SSH commands / logins, and the lab guide at `/guide/`.
Unregistered emails are told to see the instructor.

On the portal VM (`sudo` because the state lives in `/var/lib/adc-portal`):

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
