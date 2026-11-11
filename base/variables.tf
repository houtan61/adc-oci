variable "region" {
  description = "OCI region, e.g. ap-sydney-1"
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment everything is created in"
  type        = string
}

variable "name_prefix" {
  description = "Prefix for every resource name"
  type        = string
  default     = "adclab"
}

variable "availability_domain_index" {
  description = "Which availability domain to use (0 = AD-1)"
  type        = number
  default     = 0
}

variable "vcn_cidr" {
  description = "VCN CIDR. The class stack carves its subnets out of it too."
  type        = string
  default     = "10.20.0.0/16"
}

variable "mgmt_subnet_cidr" {
  description = "Subnet for the licence server and the builder (public IPs, admin-only access)"
  type        = string
  default     = "10.20.3.0/24"
}

variable "license_server_ip" {
  description = "Fixed private IP of the FS licence server. The class image points at it."
  type        = string
  default     = "10.20.3.10"
}

variable "builder_ip" {
  description = "Fixed private IP of the FS builder VM"
  type        = string
  default     = "10.20.3.20"
}

variable "admin_cidrs" {
  description = "Instructor source CIDRs allowed to the licence server / builder GUI and SSH"
  type        = list(string)
}

variable "ssh_public_key" {
  description = "Public key placed on the instances (FS ignores it; harmless)"
  type        = string
  default     = ""
}

variable "fs_image_ocid" {
  description = "Fabric Studio image used for the licence server and the builder (e.g. the official fabric-studio cloud image)"
  type        = string
}

variable "license_server_enabled" {
  description = "Create the FS licence server"
  type        = bool
  default     = true
}

variable "license_server_ocpus" {
  type    = number
  default = 2
}

variable "license_server_memory_gb" {
  type    = number
  default = 16
}

variable "builder_enabled" {
  description = "Create the FS builder VM (import the exported instance on it, then capture the class image). Set false once the image is captured."
  type        = bool
  default     = false
}

variable "builder_ocpus" {
  description = "Match what one pod needs (class stack pod_ocpus)"
  type        = number
  default     = 4
}

variable "builder_memory_gb" {
  type    = number
  default = 32
}

variable "fs_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}

variable "fs_boot_volume_gb" {
  type    = number
  default = 200
}
