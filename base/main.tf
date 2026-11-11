data "oci_identity_availability_domains" "ads" {
  compartment_id = var.compartment_ocid
}

locals {
  ad = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
}

# ---------------------------------------------------------------- network

resource "oci_core_vcn" "lab" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.name_prefix}-vcn"
  dns_label      = "adclab"
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${var.name_prefix}-igw"
  enabled        = true
}

resource "oci_core_route_table" "mgmt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${var.name_prefix}-rt-mgmt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_security_list" "mgmt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${var.name_prefix}-sl-mgmt"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  # Anything inside the VCN (pods fetch licences from the licence server)
  ingress_security_rules {
    source   = var.vcn_cidr
    protocol = "all"
  }

  # Instructor: FS GUI (443) and SSH (22)
  dynamic "ingress_security_rules" {
    for_each = { for p in setproduct(var.admin_cidrs, [22, 443]) : "${p[0]}-${p[1]}" => p }
    content {
      source   = ingress_security_rules.value[0]
      protocol = "6"
      tcp_options {
        min = ingress_security_rules.value[1]
        max = ingress_security_rules.value[1]
      }
    }
  }
}

resource "oci_core_subnet" "mgmt" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.lab.id
  cidr_block        = var.mgmt_subnet_cidr
  display_name      = "${var.name_prefix}-sn-mgmt"
  dns_label         = "mgmt"
  route_table_id    = oci_core_route_table.mgmt.id
  security_list_ids = [oci_core_security_list.mgmt.id]
}

# ---------------------------------------------------------------- FS instances

locals {
  fs_instances = merge(
    var.license_server_enabled ? {
      license = {
        name   = "${var.name_prefix}-license-server"
        ip     = var.license_server_ip
        ocpus  = var.license_server_ocpus
        memory = var.license_server_memory_gb
      }
    } : {},
    var.builder_enabled ? {
      builder = {
        name   = "${var.name_prefix}-builder"
        ip     = var.builder_ip
        ocpus  = var.builder_ocpus
        memory = var.builder_memory_gb
      }
    } : {},
  )
}

resource "oci_core_instance" "fs" {
  for_each = local.fs_instances

  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = each.value.name
  shape               = var.fs_shape

  shape_config {
    ocpus         = each.value.ocpus
    memory_in_gbs = each.value.memory
  }

  source_details {
    source_type             = "image"
    source_id               = var.fs_image_ocid
    boot_volume_size_in_gbs = var.fs_boot_volume_gb
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.mgmt.id
    private_ip       = each.value.ip
    assign_public_ip = true
    hostname_label   = each.key
  }

  metadata = var.ssh_public_key == "" ? {} : {
    ssh_authorized_keys = var.ssh_public_key
  }

  # Fabric Studio needs nested virtualisation; Secure Boot stays off (no platform_config).
  lifecycle {
    ignore_changes = [source_details[0].source_id, metadata]
  }
}
