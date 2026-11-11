terraform {
  required_version = ">= 1.5.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 5.30.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.9.0"
    }
    local = {
      source  = "hashicorp/local"
      version = ">= 2.4.0"
    }
  }
}

provider "oci" {
  region = var.region
}

# VCN, IGW and AD come from the base stack.
data "terraform_remote_state" "base" {
  backend = "local"
  config = {
    path = var.base_state_path
  }
}
