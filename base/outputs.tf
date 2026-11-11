# The class stack reads these from ../base/terraform.tfstate.

output "vcn_id" {
  value = oci_core_vcn.lab.id
}

output "vcn_cidr" {
  value = var.vcn_cidr
}

output "igw_id" {
  value = oci_core_internet_gateway.igw.id
}

output "availability_domain" {
  value = local.ad
}

output "license_server_ip" {
  value = var.license_server_ip
}

output "license_server_public_ip" {
  value = try(oci_core_instance.fs["license"].public_ip, null)
}

output "license_server_gui" {
  value = try("https://${oci_core_instance.fs["license"].public_ip}/", null)
}

output "builder_gui" {
  value = try("https://${oci_core_instance.fs["builder"].public_ip}/", null)
}

output "builder_instance_id" {
  description = "Capture the class image from this instance (OCI console > instance > More actions > Create custom image)"
  value       = try(oci_core_instance.fs["builder"].id, null)
}
