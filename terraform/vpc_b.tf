# ---------------------------------------------------------------------------
# VPC B - the private network. PRIV (connector + server) and MCU (client).
# Completely separate from VPC A: no peering, no transit gateway, no shared
# routing. The only path to the PSE is out through NAT and back over the net.
# ---------------------------------------------------------------------------

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

data "aws_ami" "windows" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["Windows_Server-2022-English-Full-Base-*"]
  }
}

resource "aws_vpc" "b" {
  cidr_block           = "10.90.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "zpa-lab-vpc-b" }
}

resource "aws_internet_gateway" "b" {
  vpc_id = aws_vpc.b.id
  tags   = { Name = "zpa-lab-b-igw" }
}

resource "aws_subnet" "b_public" {
  vpc_id            = aws_vpc.b.id
  cidr_block        = "10.90.0.0/24"
  availability_zone = "eu-central-1a"
  tags              = { Name = "zpa-lab-b-public" }
}

resource "aws_subnet" "priv" {
  vpc_id            = aws_vpc.b.id
  cidr_block        = "10.90.20.0/24"
  availability_zone = "eu-central-1a"
  tags              = { Name = "zpa-lab-priv" }
}

resource "aws_subnet" "mcu" {
  vpc_id            = aws_vpc.b.id
  cidr_block        = "10.90.30.0/24"
  availability_zone = "eu-central-1a"
  tags              = { Name = "zpa-lab-mcu" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "zpa-lab-nat-eip" }
}

resource "aws_nat_gateway" "b" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.b_public.id
  depends_on    = [aws_internet_gateway.b]
  tags          = { Name = "zpa-lab-nat" }
}

resource "aws_route_table" "b_public" {
  vpc_id = aws_vpc.b.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.b.id
  }
  tags = { Name = "zpa-lab-b-public-rt" }
}

resource "aws_route_table" "b_private" {
  vpc_id = aws_vpc.b.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.b.id
  }
  tags = { Name = "zpa-lab-b-private-rt" }
}

resource "aws_route_table_association" "b_public" {
  subnet_id      = aws_subnet.b_public.id
  route_table_id = aws_route_table.b_public.id
}

resource "aws_route_table_association" "priv" {
  subnet_id      = aws_subnet.priv.id
  route_table_id = aws_route_table.b_private.id
}

resource "aws_route_table_association" "mcu" {
  subnet_id      = aws_subnet.mcu.id
  route_table_id = aws_route_table.b_private.id
}

# --------------------------------------------------------- segmentation, layer 2
# Structural deny between PRIV and MCU. Survives a loosened security group.

resource "aws_network_acl" "priv" {
  vpc_id     = aws_vpc.b.id
  subnet_ids = [aws_subnet.priv.id]
  tags       = { Name = "zpa-lab-priv-nacl" }
}

resource "aws_network_acl_rule" "priv_in_deny_mcu" {
  network_acl_id = aws_network_acl.priv.id
  rule_number    = 100
  egress         = false
  protocol       = "-1"
  rule_action    = "deny"
  cidr_block     = "10.90.30.0/24"
}

resource "aws_network_acl_rule" "priv_in_allow" {
  network_acl_id = aws_network_acl.priv.id
  rule_number    = 200
  egress         = false
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
}

resource "aws_network_acl_rule" "priv_out_deny_mcu" {
  network_acl_id = aws_network_acl.priv.id
  rule_number    = 100
  egress         = true
  protocol       = "-1"
  rule_action    = "deny"
  cidr_block     = "10.90.30.0/24"
}

resource "aws_network_acl_rule" "priv_out_allow" {
  network_acl_id = aws_network_acl.priv.id
  rule_number    = 200
  egress         = true
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
}

resource "aws_network_acl" "mcu" {
  vpc_id     = aws_vpc.b.id
  subnet_ids = [aws_subnet.mcu.id]
  tags       = { Name = "zpa-lab-mcu-nacl" }
}

resource "aws_network_acl_rule" "mcu_in_deny_priv" {
  network_acl_id = aws_network_acl.mcu.id
  rule_number    = 100
  egress         = false
  protocol       = "-1"
  rule_action    = "deny"
  cidr_block     = "10.90.20.0/24"
}

resource "aws_network_acl_rule" "mcu_in_allow" {
  network_acl_id = aws_network_acl.mcu.id
  rule_number    = 200
  egress         = false
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
}

resource "aws_network_acl_rule" "mcu_out_deny_priv" {
  network_acl_id = aws_network_acl.mcu.id
  rule_number    = 100
  egress         = true
  protocol       = "-1"
  rule_action    = "deny"
  cidr_block     = "10.90.20.0/24"
}

resource "aws_network_acl_rule" "mcu_out_allow" {
  network_acl_id = aws_network_acl.mcu.id
  rule_number    = 200
  egress         = true
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
}

# --------------------------------------------------------- segmentation, layer 1
resource "aws_security_group" "priv_connector" {
  name        = "zpa-lab-priv-connector"
  description = "PRIV App Connector: dials outbound only, accepts nothing"
  vpc_id      = aws_vpc.b.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "zpa-lab-priv-connector" }
}

resource "aws_security_group" "server" {
  name        = "zpa-lab-server"
  description = "nginx: reachable ONLY from the PRIV App Connector. MCU is deliberately absent."
  vpc_id      = aws_vpc.b.id
  ingress {
    description     = "brokered app traffic from the connector only"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.priv_connector.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "zpa-lab-server" }
}

resource "aws_security_group" "mcu_client" {
  name        = "zpa-lab-mcu-client"
  description = "Operator client: outbound only. No inbound, no path to PRIV."
  vpc_id      = aws_vpc.b.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "zpa-lab-mcu-client" }
}

# --------------------------------------------------------- instances
resource "aws_instance" "priv_connector" {
  ami                    = var.conn_ami
  instance_type          = "t3.medium"
  subnet_id              = aws_subnet.priv.id
  vpc_security_group_ids = [aws_security_group.priv_connector.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  user_data = replace(replace(local.bootstrap, "__SERVICE__", "zpa-connector"),
  "__PARAM__", "/zpa-lab/priv-connector-provisioning-key")
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_size = 80
    volume_type = "gp3"
    encrypted   = true
  }
  tags = { Name = "zpa-lab-priv-connector" }
}

resource "aws_instance" "server" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.priv.id
  vpc_security_group_ids = [aws_security_group.server.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  user_data              = <<-BASH
    #!/bin/bash
    dnf install -y nginx
    sed -i 's/listen       80;/listen       8080;/' /etc/nginx/nginx.conf
    sed -i 's/listen       \[::\]:80;/listen       [::]:8080;/' /etc/nginx/nginx.conf
    cat > /usr/share/nginx/html/index.html <<'HTML'
    <!doctype html><meta charset="utf-8"><title>PRIV server</title>
    <h1>zpa-lab PRIV server</h1>
    <p>If you can read this, ZPA brokered you here. There is no direct route from MCU.</p>
    HTML
    systemctl enable --now nginx
  BASH
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_size = 8
    volume_type = "gp3"
    encrypted   = true
  }
  tags = { Name = "zpa-lab-server" }
}

resource "aws_instance" "mcu_client" {
  ami                    = data.aws_ami.windows.id
  instance_type          = "t3.medium"
  subnet_id              = aws_subnet.mcu.id
  vpc_security_group_ids = [aws_security_group.mcu_client.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  get_password_data      = false
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_size = 50
    volume_type = "gp3"
    encrypted   = true
  }
  tags = { Name = "zpa-lab-mcu-client" }
}

output "priv_connector_ip" { value = aws_instance.priv_connector.private_ip }
output "server_ip" { value = aws_instance.server.private_ip }
output "mcu_client_ip" { value = aws_instance.mcu_client.private_ip }
output "nat_public_ip" { value = aws_eip.nat.public_ip }
output "vpc_b_id" { value = aws_vpc.b.id }

# ---------------------------------------------------------------------------
# The cross-VPC rule. With no peering, security groups cannot reference each
# other, so VPC B is identified by the one address it leaves from: the NAT.
# Tighter than a peered rule -- exactly one /32 may reach the broker.
# ---------------------------------------------------------------------------
resource "aws_vpc_security_group_ingress_rule" "pse_from_vpc_b" {
  security_group_id = aws_security_group.pse.id
  cidr_ipv4         = "${aws_eip.nat.public_ip}/32"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "PRIV connector and MCU client, arriving via VPC B NAT"
}
