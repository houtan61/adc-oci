locals {
  base     = data.terraform_remote_state.base.outputs
  ad       = local.base.availability_domain
  hostname = trim(substr(replace(replace(var.class_name, "/[^A-Za-z0-9-]+/", "-"), "/-+/", "-"), 0, 35), "-")

  # Name students and the instructor connect to
  public_host = var.fgt_ddns_hostname != "" ? var.fgt_ddns_hostname : oci_core_instance.fgt.public_ip

  pods = {
    for n in range(1, var.student_count + 1) : format("s%02d", n) => {
      n          = n
      ip         = cidrhost(var.inside_subnet_cidr, var.pod_ip_offset + n)
      port_start = var.port_base + (n - 1) * var.port_block
    }
  }

  # One VIP per pod per service
  vips = flatten([
    for pod, p in local.pods : [
      for s in var.services : {
        name       = "${pod}-${s.name}"
        pod        = pod
        mapped_ip  = p.ip
        ext_port   = p.port_start + s.suffix
        clone_port = s.clone_port
        admin_only = s.admin_only
        service    = s.name
        proto      = s.proto
        user       = s.user != "" ? s.user : "admin"
      }
    ]
  ])

  port_last = var.port_base + var.student_count * var.port_block - 1

  # Roster: keep only email + names from whatever the registration export holds
  roster_rows = var.roster_csv == "" ? [] : csvdecode(file(var.roster_csv))
  roster = [
    for r in local.roster_rows : {
      email = lower(trimspace(try(r["Email"], r["Email Address"], r["email"], "")))
      first = trimspace(try(r["First Name"], r["First name"], r["first_name"], ""))
      last  = trimspace(try(r["Last Name"], r["Last name"], r["last_name"], ""))
    }
    if trimspace(try(r["Email"], r["Email Address"], r["email"], "")) != ""
  ]

  student_cidrs_all = distinct(concat(var.student_source_cidrs, var.admin_cidrs))
}

check "port_range" {
  assert {
    condition     = local.port_last <= 65535 && alltrue([for s in var.services : s.suffix < var.port_block])
    error_message = "Student ports run past 65535, or a service suffix is >= port_block."
  }
}
