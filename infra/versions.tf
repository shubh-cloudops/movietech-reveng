terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Every resource gets these tags, so the bill and the console show what belongs to MovieTech.
  default_tags {
    tags = {
      Project   = "movietech"
      ManagedBy = "terraform"
    }
  }
}
