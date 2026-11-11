# ---------------------------------------------------------------- general

variable "region" {
  type = string
}

variable "compartment_ocid" {
  type = string
}

variable "base_state_path" {
  description = "Path to the base stack's state file"
  type        = string
  default     = "../base/terraform.tfstate"
}

variable "name_prefix" {
  type    = string
  default = "adclab"
}

variable "class_name" {
  description = "Shown on the portal; a cleaned-up copy becomes the front FortiGate hostname"
  type        = string
  default     = "FortiADC Workshop"
}

variable "portal_title" {
  description = "Workshop title shown on the student and admin portals. Empty = class_name. Safe to change on a running class (class_name is not: it names the FGT and the bucket)."
  type        = string
  default     = "FortiADC: Local Load Balancer, GSLB, Web App publishing"
}

variable "student_count" {
  description = "Number of pods to build (registered students + a spare or two)"
  type        = number
  validation {
    condition     = var.student_count >= 1 && var.student_count <= 99
    error_message = "student_count must be 1-99."
  }
}

# ---------------------------------------------------------------- access control

variable "admin_cidrs" {
  description = "Instructor CIDRs: FGT management, portal SSH, FS GUIs, plus everything students get"
  type        = list(string)
  validation {
    condition     = alltrue([for c in var.admin_cidrs : can(cidrhost(c, 0))])
    error_message = "Every entry needs a prefix length, e.g. \"203.0.113.10/32\"."
  }
}

variable "admin_ports_open" {
  description = "true: the OCI security list leaves the admin ports (22, FGT GUI, portal SSH, admin page) open and the FortiGate enforces admin_cidrs (admin trusthosts + policy admin-in). Add an admin IP mid-class on the FGT only. false: also restrict them in OCI."
  type        = bool
  default     = true
}

variable "student_source_cidrs" {
  description = "Venue CIDRs allowed to the portal and the student ports. [\"0.0.0.0/0\"] opens them to the world."
  type        = list(string)
  validation {
    condition     = alltrue([for c in var.student_source_cidrs : can(cidrhost(c, 0))])
    error_message = "Every entry needs a prefix length, e.g. \"203.0.113.10/32\"."
  }
}

# ---------------------------------------------------------------- network (inside the base VCN)

variable "public_subnet_cidr" {
  type    = string
  default = "10.20.0.0/24"
}

variable "fgt_ip" {
  description = "Front FortiGate port1 private IP"
  type        = string
  default     = "10.20.0.10"
}

variable "inside_subnet_cidr" {
  description = "Pods + portal. Default route points at the FortiGate."
  type        = string
  default     = "10.20.1.0/24"
}

variable "portal_ip" {
  type    = string
  default = "10.20.1.10"
}

variable "pod_ip_offset" {
  description = "Pod sNN gets host address offset+NN in the inside subnet (s01 = .101)"
  type        = number
  default     = 100
}

# ---------------------------------------------------------------- front FortiGate

variable "fgt_listing_name" {
  description = "OCI Marketplace listing for the FortiGate image (used when fgt_listing_id is empty)"
  type        = string
  default     = "FortiGate Next-Gen Firewall (BYOL)"
}

variable "fgt_listing_id" {
  description = "Marketplace listing OCID; leave empty to look it up by name"
  type        = string
  default     = ""
}

variable "fgt_listing_version" {
  description = "Listing resource version, e.g. \"7.6.4_(_X64_)\""
  type        = string
}

variable "fgt_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}

variable "fgt_ocpus" {
  description = "1 OCPU = 2 vCPU; the FortiFlex config's vCPU count must cover it"
  type        = number
  default     = 1
}

variable "fgt_memory_gb" {
  type    = number
  default = 8
}

variable "fgt_license_token" {
  description = "FortiFlex token for the front FortiGate (single use per VM: new token every build, same serial keeps the DDNS name)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "fgt_ddns_hostname" {
  description = "FortiGuard DDNS name, e.g. adc-class.fortiddns.com. Empty = no DDNS (students use the public IP)."
  type        = string
  default     = ""
}

variable "fgt_admin_port" {
  description = "FortiGate HTTPS admin port (keep out of the student port range)"
  type        = number
  default     = 8443
}

# ---------------------------------------------------------------- pods

variable "class_image_ocid" {
  description = "Custom image captured from the builder (FS with the ADC topology installed + licence server URL)"
  type        = string
}

variable "pod_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}

variable "pod_ocpus" {
  type    = number
  default = 4
}

variable "pod_memory_gb" {
  type    = number
  default = 32
}

variable "pod_boot_volume_gb" {
  type    = number
  default = 200
}

variable "capacity_reservation_id" {
  description = "Optional capacity reservation for the pods (shape/OCPU/memory must match exactly)"
  type        = string
  default     = null
}

# ---------------------------------------------------------------- ports

variable "port_base" {
  description = "First external port. Student N owns port_base + (N-1)*port_block ... + port_block-1."
  type        = number
  default     = 20000
}

variable "port_block" {
  type    = number
  default = 20
}

variable "services" {
  description = <<-EOT
    One entry per port in each student's block.
      suffix     : offset inside the block (external port = block start + suffix)
      clone_port : port on the pod VM (an FS port redirection, or 443 for the FS GUI itself)
      proto      : https | ssh | other  (how the portal shows it)
      user       : login shown on the portal for ssh lines (optional)
      admin_only : true = instructor CIDRs only, not shown to students
      path       : URL path for https links (FS publishes FortiADC under /ui/)
      fs_access_id : FS access (model vm access list) behind this port. The portal sets its front port
                   (fport) to the student's port on every pod, so the FS lab console's HTTPS buttons link to
                   <host>:<student port>. FS keeps proxying on clone_port.
      show       : false = reachable by students but not listed on the portal (e.g. the FS GUI
                   that carries the web terminals)
  EOT
  type = list(object({
    name       = string
    label      = string
    suffix     = number
    clone_port = number
    proto      = string
    user       = optional(string, "")
    admin_only = optional(bool, false)
    show       = optional(bool, true)
    path       = optional(string, "/")
    fs_access_id = optional(number)
  }))
  default = [
    # FS GUI: students log in as guest (lab console link on the portal), so not admin_only, but not listed
    { name = "fsgui", label = "Fabric Studio GUI", suffix = 0, clone_port = 443, proto = "https", show = false },
    # FS HTTPS accesses (reverse proxy): must be PUBLIC in the image, see README
    { name = "fadc-gui", label = "FAD-Cannes GUI", suffix = 1, clone_port = 13002, proto = "https", path = "/ui/", fs_access_id = 9 },
    { name = "fads-gui", label = "FAD-Sunnyvale GUI", suffix = 3, clone_port = 13004, proto = "https", path = "/ui/", fs_access_id = 21 },
    { name = "fgt-gui", label = "Internet FortiGate GUI", suffix = 5, clone_port = 13006, proto = "https", fs_access_id = 33 },
  ]
}

# ---------------------------------------------------------------- browser terminals

variable "web_terminals" {
  description = <<-EOT
    Fabric Studio web terminals (ttyd) listed on the portal, opened through the pod's FS GUI port
    (the service with suffix fs_gui_suffix): https://<host>:<block start + suffix><path>
    Path as FS shows it, e.g. /ttyd/ssh/FADCa1/
  EOT
  type = list(object({
    label = string
    path  = string
  }))
  default = []
}

variable "fs_gui_suffix" {
  type    = number
  default = 0
}

variable "fs_guest_user" {
  description = "FS guest account students log in with: topology, guide, SERIAL/SSH/VNC to every device, no admin rights. Empty = no lab console link."
  type        = string
  default     = "guest"
}

variable "fs_guest_password" {
  description = "Shown to students on the portal"
  type        = string
  default     = ""
}

# ---------------------------------------------------------------- portal

variable "portal_passphrase" {
  description = "Class passphrase students type at check-in (case and spaces ignored)"
  type        = string
  sensitive   = true
}

variable "roster_csv" {
  description = "Optional registration export (CSV with Email / First Name / Last Name columns). Empty = open check-in or import later with adc-portal roster import."
  type        = string
  default     = ""
}

variable "guide_zip" {
  description = "Lab guide zip (one top-level folder with index.html)"
  type        = string
  default     = "../guide/fortiadc-global-app-delivery-docs.zip"
}

variable "lab_credentials" {
  description = "Shown on the portal's pod page"
  type = list(object({
    device   = string
    user     = string
    password = string
  }))
  default = [
    { device = "FortiADC (Cannes, Sunnyvale)", user = "admin", password = "Fortinet123!@" },
    { device = "Internet FortiGate", user = "admin", password = "Fortinet123!@" },
    { device = "zClient, DNS servers, web servers", user = "root", password = "Fortinet123!@" },
  ]
}

variable "portal_admin_port" {
  description = "Admin page (roster upload, live check-in stats, assign/release pods): instructor CIDRs only, same port on the FGT and the portal VM"
  type        = number
  default     = 8005
}

variable "portal_admin_password" {
  description = "Password for the admin page (user admin, login form on the page). Empty = admin page off."
  type        = string
  sensitive   = true
  default     = ""
}

variable "fs_admin_password" {
  description = "Pods' Fabric Studio admin password, for the admin page's power-on buttons and pods-power-on. Empty = buttons off."
  type        = string
  sensitive   = true
  default     = ""
}

variable "license_per_pod" {
  description = "Licences each pod takes from the licence server pool, per FortiFlex pool type (admin page compares the pool with pods x this). The topology has FAD-Cannes(+node2), FAD-Sunnyvale(+node2) and the internet FGT."
  type        = map(number)
  default = {
    FAD = 4
    FGT = 1
  }
}

variable "fabric_id" {
  description = "Fabric ID in the class image (FS CLI: runtime fabric detail)"
  type        = number
  default     = 1
}

variable "portal_ssh_port" {
  description = "External port on the FortiGate for SSH to the portal VM (instructor only)"
  type        = number
  default     = 2222
}

variable "portal_ocpus" {
  type    = number
  default = 1
}

variable "portal_memory_gb" {
  type    = number
  default = 8
}

variable "ssh_public_key" {
  description = "Your public key for ubuntu@portal"
  type        = string
}

variable "par_valid_hours" {
  description = "How long the portal VM's read link to its bucket stays valid (it only needs it at boot)"
  type        = number
  default     = 72
}
