# Remote state in S3 with native locking (OpenTofu >= 1.10; no DynamoDB table).
#
# Partial configuration on purpose: bucket, key, region and locking are supplied at
# init time so this file carries nothing account-specific:
#
#   tofu init \
#     -backend-config="bucket=zs-lab-tfstate-<aws-account-id>" \
#     -backend-config="key=usecases/zpa-private-service-edge/terraform.tfstate" \
#     -backend-config="region=eu-central-1" \
#     -backend-config="use_lockfile=true"
#
# Switchboard passes exactly these. Running the lab by hand from a laptop uses the
# same command, so both share one state and one lock.
terraform {
  backend "s3" {}
}
