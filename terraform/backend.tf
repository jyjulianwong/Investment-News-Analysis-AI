terraform {
  backend "s3" {
    bucket         = "jyjulianwong-ina-terraform-state"
    key            = "prod/terraform.tfstate"
    region         = "eu-west-2"
    dynamodb_table = "jyjulianwong-ina-terraform-lock"
    encrypt        = true
  }
}
