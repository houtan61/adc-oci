# Check-in portal: a small Ubuntu VM in the inside subnet. Its files (portal
# script, config, lab guide) go through a private bucket, because OCI user_data
# is capped at 32 KB. The VM pulls them at boot through a read-only PAR.

data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.compartment_ocid
}

locals {
  ns           = data.oci_objectstorage_namespace.ns.namespace
  guide_object = "guide-${substr(filemd5(var.guide_zip), 0, 12)}.zip"

  portal_config = {
    class_name     = var.class_name
    passphrase     = var.portal_passphrase
    public_host    = local.public_host
    port_base      = var.port_base
    port_block     = var.port_block
    services       = [for s in var.services : s if !s.admin_only && s.show]
    web_terminals  = var.web_terminals
    fs_gui_suffix  = var.fs_gui_suffix
    fs_guest       = var.fs_guest_user == "" ? null : { user = var.fs_guest_user, password = var.fs_guest_password }
    all_services   = var.services
    pods           = [for k, p in local.pods : { id = k, ip = p.ip, port_start = p.port_start }]
    roster         = local.roster
    credentials    = var.lab_credentials
    guide_object   = local.guide_object
    fgt_admin_port = var.fgt_admin_port
    admin_password = var.portal_admin_password
    fs_password    = var.fs_admin_password
    fabric_id      = var.fabric_id
  }
}

resource "oci_objectstorage_bucket" "portal" {
  compartment_id = var.compartment_ocid
  namespace      = local.ns
  name           = "${var.name_prefix}-${lower(local.hostname)}-portal"
  access_type    = "NoPublicAccess"
}

resource "oci_objectstorage_object" "text" {
  for_each = {
    "adc_portal.py"     = file("${path.module}/../portal/adc_portal.py")
    "pods_check.py"     = file("${path.module}/../portal/pods_check.py")
    "pods_power_on.sh"  = file("${path.module}/../portal/pods_power_on.sh")
    "config.json"       = jsonencode(local.portal_config)
  }
  namespace = local.ns
  bucket    = oci_objectstorage_bucket.portal.name
  object    = each.key
  content   = each.value
}

resource "oci_objectstorage_object" "guide" {
  namespace = local.ns
  bucket    = oci_objectstorage_bucket.portal.name
  object    = local.guide_object
  source    = var.guide_zip
}

resource "time_offset" "par_expiry" {
  offset_hours = var.par_valid_hours
}

resource "oci_objectstorage_preauthrequest" "portal" {
  namespace             = local.ns
  bucket                = oci_objectstorage_bucket.portal.name
  name                  = "portal-boot-read"
  access_type           = "AnyObjectRead"
  bucket_listing_action = "Deny"
  time_expires          = time_offset.par_expiry.rfc3339
}

locals {
  # access_uri ends in /o/ for bucket-level PARs
  par_base = "https://objectstorage.${var.region}.oraclecloud.com${oci_objectstorage_preauthrequest.portal.access_uri}"
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = "VM.Standard.E5.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"

  filter {
    name   = "display_name"
    values = ["^Canonical-Ubuntu-24\\.04-[0-9].*"]
    regex  = true
  }
}

resource "oci_core_instance" "portal" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = "${var.name_prefix}-portal"
  shape               = "VM.Standard.E5.Flex"

  shape_config {
    ocpus         = var.portal_ocpus
    memory_in_gbs = var.portal_memory_gb
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ubuntu.images[0].id
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.inside.id
    private_ip       = var.portal_ip
    assign_public_ip = false
    hostname_label   = "portal"
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data = base64encode(templatefile("${path.module}/portal-cloudinit.tftpl", {
      par_base     = local.par_base
      guide_object = local.guide_object
      admin_port   = var.portal_admin_port
    }))
  }

  lifecycle {
    ignore_changes = [source_details[0].source_id]
  }

  depends_on = [
    oci_objectstorage_object.text,
    oci_objectstorage_object.guide,
    oci_core_instance.fgt,
  ]
}
