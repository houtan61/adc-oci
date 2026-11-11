terraform {
  required_version = ">= 1.5.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 5.30.0"
    }
  }
}

# In OCI Cloud Shell the provider picks up your session automatically;
# only the region needs setting.
provider "oci" {
  region = var.region
}
