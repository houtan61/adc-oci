# ---------------------------------------------------------------- public subnet (front FortiGate)

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = local.base.vcn_id
  display_name   = "${var.name_prefix}-rt-public"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = local.base.igw_id
  }
}

resource "oci_core_security_list" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = local.base.vcn_id
  display_name   = "${var.name_prefix}-sl-public"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  # Pods and portal going out through the FortiGate
  ingress_security_rules {
    source   = local.base.vcn_cidr
    protocol = "all"
  }

  # Path MTU discovery
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "1"
    icmp_options {
      type = 3
      code = 4
    }
  }

  # Students (+ instructor): portal on 80 and the student port blocks.
  # The FortiGate policies narrow it further (FS GUI = instructor only).
  dynamic "ingress_security_rules" {
    for_each = local.student_cidrs_all
    content {
      source   = ingress_security_rules.value
      protocol = "6"
      tcp_options {
        min = 80
        max = 80
      }
    }
  }

  dynamic "ingress_security_rules" {
    for_each = local.student_cidrs_all
    content {
      source   = ingress_security_rules.value
      protocol = "6"
      tcp_options {
        min = var.port_base
        max = local.port_last
      }
    }
  }

  # Instructor: FortiGate GUI/SSH and SSH to the portal
  dynamic "ingress_security_rules" {
    for_each = { for p in setproduct(var.admin_cidrs, [22, var.fgt_admin_port, var.portal_ssh_port]) : "${p[0]}-${p[1]}" => p }
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

resource "oci_core_subnet" "public" {
  compartment_id    = var.compartment_ocid
  vcn_id            = local.base.vcn_id
  cidr_block        = var.public_subnet_cidr
  display_name      = "${var.name_prefix}-sn-public"
  dns_label         = "public"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.public.id]
}

# ---------------------------------------------------------------- inside subnet (pods + portal)

data "oci_core_private_ips" "fgt" {
  ip_address = var.fgt_ip
  subnet_id  = oci_core_subnet.public.id
  depends_on = [oci_core_instance.fgt]
}

resource "oci_core_route_table" "inside" {
  compartment_id = var.compartment_ocid
  vcn_id         = local.base.vcn_id
  display_name   = "${var.name_prefix}-rt-inside"

  # Everything leaves via the FortiGate, so VIP replies (no SNAT) go back the same way.
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = data.oci_core_private_ips.fgt.private_ips[0].id
  }
}

resource "oci_core_security_list" "inside" {
  compartment_id = var.compartment_ocid
  vcn_id         = local.base.vcn_id
  display_name   = "${var.name_prefix}-sl-inside"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  # Students arrive with their real source IP (no SNAT); the FortiGate already filtered them.
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "all"
  }
}

resource "oci_core_subnet" "inside" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = local.base.vcn_id
  cidr_block                 = var.inside_subnet_cidr
  display_name               = "${var.name_prefix}-sn-inside"
  dns_label                  = "inside"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.inside.id
  security_list_ids          = [oci_core_security_list.inside.id]
}
