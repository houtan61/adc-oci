output "fgt_public_ip" {
  value = oci_core_instance.fgt.public_ip
}

output "fgt_admin_url" {
  description = "First login: admin / the instance OCID (fgt_instance_id)"
  value       = "https://${oci_core_instance.fgt.public_ip}:${var.fgt_admin_port}/"
}

output "fgt_instance_id" {
  value = oci_core_instance.fgt.id
}

output "portal_url" {
  value = "http://${local.public_host}/"
}

output "portal_ssh" {
  value = "ssh -p ${var.portal_ssh_port} ubuntu@${local.public_host}"
}

output "pods" {
  value = { for k, p in local.pods : k => "${p.ip}  ports ${p.port_start}-${p.port_start + var.port_block - 1}  FS GUI https://${local.public_host}:${p.port_start}/" }
}

resource "local_file" "access_csv" {
  filename = "${path.module}/out/access.csv"
  content = join("\n", concat(
    ["pod,service,url"],
    [for v in local.vips : format("%s,%s,%s", v.pod, v.service,
      v.proto == "ssh" ? "ssh -p ${v.ext_port} ${v.user}@${local.public_host}" :
      v.proto == "https" ? "https://${local.public_host}:${v.ext_port}/" :
      "${local.public_host}:${v.ext_port}"
    )],
    [""]
  ))
}
