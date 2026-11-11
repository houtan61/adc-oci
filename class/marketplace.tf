# FortiGate image from the OCI Marketplace. The agreement + subscription are
# required, otherwise LaunchInstance fails with 404-NotAuthorizedOrNotFound.

data "oci_core_app_catalog_listings" "fgt" {
  count        = var.fgt_listing_id == "" ? 1 : 0
  display_name = var.fgt_listing_name
}

locals {
  fgt_listing_id = var.fgt_listing_id != "" ? var.fgt_listing_id : data.oci_core_app_catalog_listings.fgt[0].app_catalog_listings[0].listing_id
}

data "oci_core_app_catalog_listing_resource_version" "fgt" {
  listing_id       = local.fgt_listing_id
  resource_version = var.fgt_listing_version
}

resource "oci_core_app_catalog_listing_resource_version_agreement" "fgt" {
  listing_id               = local.fgt_listing_id
  listing_resource_version = var.fgt_listing_version
}

resource "oci_core_app_catalog_subscription" "fgt" {
  compartment_id           = var.compartment_ocid
  listing_id               = local.fgt_listing_id
  listing_resource_version = var.fgt_listing_version
  eula_link                = oci_core_app_catalog_listing_resource_version_agreement.fgt.eula_link
  oracle_terms_of_use_link = oci_core_app_catalog_listing_resource_version_agreement.fgt.oracle_terms_of_use_link
  signature                = oci_core_app_catalog_listing_resource_version_agreement.fgt.signature
  time_retrieved           = oci_core_app_catalog_listing_resource_version_agreement.fgt.time_retrieved
}
