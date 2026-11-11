# One Fabric Studio clone per student, no public IP. Reached only through the
# front FortiGate VIPs; default route is the FortiGate (see network.tf).

resource "oci_core_instance" "pod" {
  for_each = local.pods

  compartment_id          = var.compartment_ocid
  availability_domain     = local.ad
  display_name            = "${var.name_prefix}-${each.key}"
  shape                   = var.pod_shape
  capacity_reservation_id = var.capacity_reservation_id

  shape_config {
    ocpus         = var.pod_ocpus
    memory_in_gbs = var.pod_memory_gb
  }

  source_details {
    source_type             = "image"
    source_id               = var.class_image_ocid
    boot_volume_size_in_gbs = var.pod_boot_volume_gb
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.inside.id
    private_ip       = each.value.ip
    assign_public_ip = false
    hostname_label   = each.key
  }

  freeform_tags = {
    class = local.hostname
    pod   = each.key
  }
}
