terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}
provider "aws" {
  region = "eu-central-1"
  default_tags {
    tags = {
      Project   = "zpa-pse-lab"
      ManagedBy = "opentofu"
      Owner     = "nujma"
    }
  }
}

variable "pse_ami" { default = "ami-07811cc3852902146" }
variable "conn_ami" { default = "ami-0aaa3d92aff1df4a0" }

# ---------------------------------------------------------------- network
resource "aws_vpc" "lab" {
  cidr_block           = "10.91.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "zpa-lab-vpc-a" }
}
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.lab.id
  tags   = { Name = "zpa-lab-igw" }
}
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = "10.91.10.0/24"
  availability_zone       = "eu-central-1a"
  map_public_ip_on_launch = true
  tags                    = { Name = "zpa-lab-public" }
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.lab.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = { Name = "zpa-lab-public-rt" }
}
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------- security
resource "aws_security_group" "pse" {
  name        = "zpa-lab-pse"
  description = "PSE: accepts 443 from inside the lab VPC only"
  vpc_id      = aws_vpc.lab.id
  ingress {
    description = "Client Connector and App Connector dial the broker"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.lab.cidr_block]
  }
  egress {
    description = "Control-plane tunnel to the ZPA cloud"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "zpa-lab-pse" }
}
resource "aws_security_group" "connector" {
  name        = "zpa-lab-connector"
  description = "App Connector: dials outbound only, accepts nothing"
  vpc_id      = aws_vpc.lab.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "zpa-lab-connector" }
}

# ---------------------------------------------------------------- iam
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "node" {
  name               = "zpa-lab-node"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}
resource "aws_iam_role_policy" "ssm_read" {
  name = "read-provisioning-keys"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ssm:GetParameter"],
      Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/zpa-lab/*" },
      # The ZPA AMIs ship no SSM agent and we set no key pair, so a failing
      # instance cannot be inspected from outside. Let it report on itself.
      { Effect = "Allow", Action = ["ssm:PutParameter"],
      Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/zpa-lab/debug/*" },
      { Effect = "Allow", Action = ["kms:Decrypt"], Resource = "*",
      Condition = { StringEquals = { "kms:ViaService" = "ssm.eu-central-1.amazonaws.com" } } }
    ]
  })
}
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "node" {
  name = "zpa-lab-node"
  role = aws_iam_role.node.name
}

# ---------------------------------------------------------------- instances
locals {
  # Waits for the key to appear in SSM, then enrols. No secret is embedded here,
  # so nothing sensitive lands in user_data or in tofu state.
  # Mirrors scripts/user_data_zscaler.sh (the Marketplace-AMI variant, NOT the
  # rhel9 one) from zscaler's app-connector and private-service-edge modules.
  # The two components read the key from DIFFERENT paths -- App Connector from
  # /opt/zscaler/var/provision_key, Service Edge from
  # /opt/zscaler/var/service-edge/provision_key -- hence __KEYPATH__ per instance.
  # The file must be 644: the service does not run as root.
  bootstrap = <<-BASH
    #!/bin/bash
    exec > >(tee /var/log/zpa-bootstrap.log) 2>&1
    set -uo pipefail
    SVC="__SERVICE__"; PARAM="__PARAM__"; KEYPATH="__KEYPATH__"

    # STOP FIRST -- before anything slow. The image auto-starts the service at
    # boot; if it is left running while we install the CLI and poll SSM (~60s)
    # it commits to OAuth2 provisioning and then ignores the key file entirely.
    # Zscaler's own script gets away with stopping later only because it has the
    # key inline and stops within seconds. We fetch, so we must stop up front.
    systemctl stop "$SVC" 2>/dev/null || true

    if ! command -v aws >/dev/null 2>&1; then
      dnf install -y unzip >/dev/null 2>&1 || true
      curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
        && unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install >/dev/null 2>&1
    fi

    KEY=""
    for i in $(seq 1 60); do
      KEY=$(aws ssm get-parameter --name "$PARAM" --with-decryption \
            --region eu-central-1 --query Parameter.Value --output text 2>/dev/null || true)
      if [ -n "$${KEY:-}" ] && [ "$KEY" != "None" ]; then break; fi
      echo "waiting for $PARAM ($i/60)"; sleep 10
    done
    if [ -z "$${KEY:-}" ] || [ "$KEY" = "None" ]; then echo "FATAL: key never appeared"; exit 1; fi

    systemctl stop "$SVC" 2>/dev/null || true
    install -d -m 755 "$(dirname "$KEYPATH")"
    echo "$KEY" > "$KEYPATH"
    chmod 644 "$KEYPATH"
    systemctl start "$SVC"

    # Report state back through SSM. The console buffer only holds the last 64KB
    # and the OAuth banner floods it within minutes, so console output is not a
    # reliable channel here. Never echoes the key itself -- only its length.
    HOST=$(hostname)
    REPORT=$(
      echo "svc=$SVC"
      echo "keyfile=$(ls -l $KEYPATH 2>&1 | tr -s ' ')"
      echo "keybytes=$(wc -c < $KEYPATH 2>/dev/null || echo missing)"
      echo "active=$(systemctl is-active $SVC 2>&1)"
      echo "enabled=$(systemctl is-enabled $SVC 2>&1)"
      echo "issue=$(grep -Eo '[A-Z0-9]{5}-[A-Z0-9]{5}' /etc/issue 2>/dev/null | head -1)"
      echo "units=$(systemctl list-units --type=service --no-legend 2>/dev/null | grep -i zpa | tr -s ' ' | cut -d' ' -f1,4 | tr '\n' ',')"
      echo "varfiles=$(ls /opt/zscaler/var 2>&1 | tr '\n' ',')"
      echo "journal=$(journalctl -u $SVC -n 12 --no-pager 2>&1 | tr '\n' '|' | cut -c1-900)"
    )
    aws ssm put-parameter --region eu-central-1 --name "/zpa-lab/debug/$HOST" \
      --type String --overwrite --value "$REPORT" >/dev/null 2>&1 \
      && echo "diagnostics published to /zpa-lab/debug/$HOST"

    nohup dnf update -y >/dev/null 2>&1 &
    echo "bootstrap finished; provision_key written, $SVC restarted"
  BASH
}

resource "aws_instance" "pse" {
  ami                    = var.pse_ami
  instance_type          = "m5.large"
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.pse.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  user_data = replace(replace(replace(local.bootstrap,
    "__SERVICE__", "zpa-service-edge"),
    "__PARAM__", "/zpa-lab/pse-provisioning-key"),
  "__KEYPATH__", "/opt/zscaler/var/service-edge/provision_key")
  metadata_options { http_tokens = "required" }
  root_block_device {
    volume_size = 80
    volume_type = "gp3"
    encrypted   = true
  }
  tags = { Name = "zpa-lab-pse" }
}
resource "aws_eip" "pse" {
  instance = aws_instance.pse.id
  domain   = "vpc"
  tags     = { Name = "zpa-lab-pse-eip" }
}

resource "aws_instance" "connector" {
  ami                    = var.conn_ami
  instance_type          = "t3.medium"
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.connector.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  user_data = replace(replace(replace(local.bootstrap,
    "__SERVICE__", "zpa-connector"),
    "__PARAM__", "/zpa-lab/connector-provisioning-key"),
  "__KEYPATH__", "/opt/zscaler/var/provision_key")
  metadata_options { http_tokens = "required" }
  root_block_device {
    volume_size = 80
    volume_type = "gp3"
    encrypted   = true
  }
  tags = { Name = "zpa-lab-connector" }
}

output "pse_public_ip" { value = aws_eip.pse.public_ip }
output "pse_private_ip" { value = aws_instance.pse.private_ip }
output "connector_ip" { value = aws_instance.connector.private_ip }
output "vpc_id" { value = aws_vpc.lab.id }

variable "region" {
  description = "AWS region for the lab"
  type        = string
  default     = "eu-central-1"
}

data "aws_caller_identity" "current" {}
