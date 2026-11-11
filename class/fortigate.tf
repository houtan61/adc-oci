# One-arm front FortiGate (port1 only). OCI hot-attaches secondary VNICs after
# boot, so a port2 bootstrap would be lost; one interface avoids that.
#
# Bootstrap note: an unlicensed FortiGate-VM only accepts 3 firewall policies and
# the config part runs before FortiFlex licenses it, so the bootstrap uses exactly 3.
#
# First login: https://<public ip>:<fgt_admin_port>  admin / <instance OCID>

resource "oci_core_instance" "fgt" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = "${var.name_prefix}-fgt"
  shape               = var.fgt_shape

  shape_config {
    ocpus         = var.fgt_ocpus
    memory_in_gbs = var.fgt_memory_gb
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_app_catalog_listing_resource_version.fgt.listing_resource_id
  }

  create_vnic_details {
    subnet_id              = oci_core_subnet.public.id
    private_ip             = var.fgt_ip
    assign_public_ip       = true
    skip_source_dest_check = true
    hostname_label         = "fgt"
  }

  metadata = {
    user_data = base64encode(templatefile("${path.module}/fgt-bootstrap.tftpl", {
      hostname        = local.hostname
      admin_port      = var.fgt_admin_port
      admin_cidrs     = var.admin_cidrs
      student_cidrs   = var.student_source_cidrs
      inside_cidr     = var.inside_subnet_cidr
      fgt_ip          = var.fgt_ip
      portal_ip       = var.portal_ip
      portal_ssh_port = var.portal_ssh_port
      vips            = local.vips
      ddns_hostname   = var.fgt_ddns_hostname
      license_token   = var.fgt_license_token
    }))
  }

  depends_on = [oci_core_app_catalog_subscription.fgt]
}
