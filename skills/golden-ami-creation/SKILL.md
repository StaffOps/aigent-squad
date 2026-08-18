---
name: golden-ami-creation
description: "Use when building hardened AMIs with Packer and Ansible, applying CIS benchmarks, managing AMI lifecycle, or sharing images cross-account. Covers Ubuntu 22.04 hardening, MongoDB-ready variants, Trivy filesystem scan, SSM Parameter Store for AMI IDs, and <org> automation patterns."
keywords: [golden-ami-creation, golden, ami, creation, "golden ami", "ami creation", trivy, cis, ssm]
---
# Golden AMI Creation (Packer + Ansible)

<org> builds hardened AMIs for EC2 workloads using Packer (orchestration) + Ansible (configuration). All AMIs follow CIS Ubuntu Benchmark Level 1.

## Location

**Path**: `<workspace>/01-DEVOPS/AUTOMATIONS/AMIS/`

## AMI variants

| Variant | Base | Purpose | Extras |
|---------|------|---------|--------|
| `<org>-ubuntu-hardened` | Ubuntu 22.04 LTS | General purpose EC2 | CIS hardened, SSM agent, CloudWatch agent |
| `<org>-mongodb-ready` | Ubuntu 22.04 LTS | MongoDB instances | XFS filesystem, ulimits, THP disabled, numactl |

## Packer template structure

```hcl
# packer.pkr.hcl
packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
    ansible = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/ansible"
    }
  }
}

source "amazon-ebs" "ubuntu" {
  ami_name      = "<org>-ubuntu-hardened-{{timestamp}}"
  instance_type = "t3.medium"
  region        = var.region

  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    owners      = ["099720109477"]  # Canonical
    most_recent = true
  }

  ssh_username = "ubuntu"

  # Encryption mandatory
  encrypt_boot = true
  kms_key_id   = var.kms_key_id

  # Tags (mandatory)
  tags = {
    Name        = "<org>-ubuntu-hardened-{{timestamp}}"
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "INFRASTRUCTURE"
    CostProject = "GOLDEN-AMI"
    BaseOS      = "Ubuntu 22.04"
    BuildDate   = "{{timestamp}}"
    Arch        = "amd64"
  }

  # Share cross-account
  ami_users = var.shared_account_ids
}

build {
  sources = ["source.amazon-ebs.ubuntu"]

  provisioner "ansible" {
    playbook_file = "./ansible/hardening.yml"
    extra_arguments = [
      "--extra-vars", "ami_variant=hardened"
    ]
  }

  # Post-build: Trivy filesystem scan
  provisioner "shell" {
    inline = [
      "curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /tmp",
      "/tmp/trivy fs --exit-code 1 --severity CRITICAL --scanners vuln /"
    ]
  }

  post-processor "manifest" {
    output     = "manifest.json"
    strip_path = true
  }
}
```

### Multi-arch (amd64 + arm64)

```hcl
# Build both architectures
source "amazon-ebs" "ubuntu-arm64" {
  ami_name      = "<org>-ubuntu-hardened-arm64-{{timestamp}}"
  instance_type = "t4g.medium"  # Graviton

  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-arm64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    owners      = ["099720109477"]
    most_recent = true
  }

  tags = {
    Arch = "arm64"
    # ... same mandatory tags
  }
}

build {
  sources = [
    "source.amazon-ebs.ubuntu",
    "source.amazon-ebs.ubuntu-arm64"
  ]
  # ... same provisioners
}
```

## Ansible hardening playbook

### CIS Ubuntu Benchmark Level 1

```yaml
# ansible/hardening.yml
---
- name: CIS Ubuntu 22.04 Hardening
  hosts: all
  become: true
  roles:
    - role: cis-hardening
    - role: ssm-agent
    - role: cloudwatch-agent
    - role: common-packages

# ansible/roles/cis-hardening/tasks/main.yml
---
# 1. Filesystem configuration
- name: Disable unused filesystems
  copy:
    dest: /etc/modprobe.d/cis-disabled-fs.conf
    content: |
      install cramfs /bin/true
      install freevxfs /bin/true
      install jffs2 /bin/true
      install hfs /bin/true
      install hfsplus /bin/true
      install squashfs /bin/true
      install udf /bin/true

# 2. SSH hardening
- name: Configure sshd
  template:
    src: sshd_config.j2
    dest: /etc/ssh/sshd_config
    mode: '0600'
  notify: restart sshd

# 3. Kernel parameters
- name: Set kernel security parameters
  sysctl:
    name: "{{ item.key }}"
    value: "{{ item.value }}"
    sysctl_set: true
    reload: true
  loop:
    - { key: "net.ipv4.conf.all.send_redirects", value: "0" }
    - { key: "net.ipv4.conf.default.send_redirects", value: "0" }
    - { key: "net.ipv4.conf.all.accept_redirects", value: "0" }
    - { key: "net.ipv4.conf.all.log_martians", value: "1" }
    - { key: "net.ipv4.icmp_echo_ignore_broadcasts", value: "1" }
    - { key: "kernel.randomize_va_space", value: "2" }
    - { key: "fs.suid_dumpable", value: "0" }

# 4. Audit rules (auditd)
- name: Configure audit rules
  copy:
    dest: /etc/audit/rules.d/cis.rules
    content: |
      -w /etc/passwd -p wa -k identity
      -w /etc/group -p wa -k identity
      -w /etc/shadow -p wa -k identity
      -w /etc/sudoers -p wa -k sudoers
      -w /var/log/auth.log -p wa -k auth_log
      -a always,exit -F arch=b64 -S execve -k exec
  notify: restart auditd

# 5. Password policy
- name: Configure password quality
  lineinfile:
    path: /etc/security/pwquality.conf
    regexp: "^{{ item.key }}"
    line: "{{ item.key }} = {{ item.value }}"
  loop:
    - { key: "minlen", value: "14" }
    - { key: "dcredit", value: "-1" }
    - { key: "ucredit", value: "-1" }
    - { key: "ocredit", value: "-1" }
    - { key: "lcredit", value: "-1" }
```

### SSH configuration template

```
# ansible/roles/cis-hardening/templates/sshd_config.j2
Protocol 2
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
X11Forwarding no
MaxAuthTries 4
ClientAliveInterval 300
ClientAliveCountMax 0
LoginGraceTime 60
AllowAgentForwarding no
AllowTcpForwarding no
Banner /etc/issue.net
UsePAM yes
```

### MongoDB-ready variant

```yaml
# ansible/roles/mongodb-ready/tasks/main.yml
---
- name: Disable Transparent Huge Pages
  copy:
    dest: /etc/systemd/system/disable-thp.service
    content: |
      [Unit]
      Description=Disable THP
      [Service]
      Type=oneshot
      ExecStart=/bin/sh -c "echo never > /sys/kernel/mm/transparent_hugepage/enabled"
      ExecStart=/bin/sh -c "echo never > /sys/kernel/mm/transparent_hugepage/defrag"
      [Install]
      WantedBy=multi-user.target

- name: Set MongoDB ulimits
  copy:
    dest: /etc/security/limits.d/mongodb.conf
    content: |
      mongod soft nofile 64000
      mongod hard nofile 64000
      mongod soft nproc 64000
      mongod hard nproc 64000

- name: Install numactl
  apt:
    name: numactl
    state: present

- name: Configure XFS mount options
  mount:
    path: /data
    src: /dev/xvdf
    fstype: xfs
    opts: "defaults,noatime"
    state: present
```

## AMI lifecycle

### Build → Scan → Tag → Share → Deprecate

```
Build (Packer) → Trivy fs scan → Tag (mandatory) → Share cross-account → SSM Parameter → Deprecate old
```

### SSM Parameter Store (latest AMI ID)

```bash
# Store latest AMI ID per variant/arch/region
# Pattern: /<org>/ami/<variant>/<arch>/latest
aws ssm put-parameter \
  --name "/<org>/ami/ubuntu-hardened/amd64/latest" \
  --value "ami-0abc123def456" \
  --type String \
  --overwrite \
  --region us-east-1
```

### Consume in Terraform

```hcl
data "aws_ssm_parameter" "ami" {
  name = "/<org>/ami/ubuntu-hardened/amd64/latest"
}

resource "aws_instance" "this" {
  ami           = data.aws_ssm_parameter.ami.value
  instance_type = "m6i.large"
  # ...
}
```

### Auto-deprecation

```bash
# Deprecate AMIs older than 90 days
aws ec2 describe-images \
  --owners self \
  --filters "Name=tag:CostProject,Values=GOLDEN-AMI" \
  --query "Images[?CreationDate<='$(date -d '-90 days' +%Y-%m-%d)'].ImageId" \
  --output text | while read AMI_ID; do
    aws ec2 enable-image-deprecation \
      --image-id "$AMI_ID" \
      --deprecate-at "$(date -d '+30 days' --iso-8601=seconds)"
done
```

### Cross-account sharing

```hcl
# In Packer template
ami_users = [
  "111111111111",  # dev account
  "222222222222",  # staging account
  "333333333333",  # production account
]
```

## CI/CD pipeline (GitLab)

```yaml
stages:
  - validate
  - build
  - scan
  - publish

validate:packer:
  stage: validate
  script:
    - packer init . && packer validate . && packer fmt -check .

build:ami:
  stage: build
  script:
    - packer build -var "region=us-east-1" -var "kms_key_id=${KMS_KEY}" .
    - AMI_ID=$(jq -r '.builds[-1].artifact_id' manifest.json | cut -d: -f2)
    - echo "AMI_ID=${AMI_ID}" >> build.env
  artifacts:
    reports:
      dotenv: build.env

scan:ami:
  stage: scan
  needs: [build:ami]
  script:
    - INSTANCE_ID=$(aws ec2 run-instances --image-id ${AMI_ID} --instance-type t3.micro --query 'Instances[0].InstanceId' --output text)
    - aws ec2 wait instance-status-ok --instance-ids ${INSTANCE_ID}
    - ssh ubuntu@${INSTANCE_IP} "sudo trivy fs --exit-code 1 --severity CRITICAL /"
    - aws ec2 terminate-instances --instance-ids ${INSTANCE_ID}

publish:ssm:
  stage: publish
  needs: [scan:ami]
  script:
    - aws ssm put-parameter --name "/<org>/ami/ubuntu-hardened/amd64/latest" --value "${AMI_ID}" --type String --overwrite
```

## Encryption

### EBS default encryption (account-level)

```hcl
resource "aws_ebs_default_kms_key" "this" {
  key_arn = aws_kms_key.ebs_default.arn
}

resource "aws_ebs_encryption_by_default" "this" {
  enabled = true
}
```

### Packer encryption

Always set `encrypt_boot = true` in Packer source:
```hcl
source "amazon-ebs" "ubuntu" {
  encrypt_boot = true
  kms_key_id   = var.kms_key_id  # Use <org> default KMS key
}
```

## Anti-patterns

- ❌ AMIs without EBS encryption (data at rest exposure)
- ❌ Golden AMIs without auto-deprecation schedule (stale images with unpatched CVEs)
- ❌ Hardcoded package versions in Ansible (use `state: latest` with pinned repos)
- ❌ No Trivy filesystem scan post-build (ship vulnerabilities unknowingly)
- ❌ Manual AMI sharing (use Packer `ami_users` or post-processor)
- ❌ AMI IDs hardcoded in Terraform (use SSM Parameter Store lookup)
- ❌ Single-arch AMIs when Graviton instances are available
- ❌ Skipping CIS hardening for "internal" instances
- ❌ Root SSH access enabled (`PermitRootLogin yes`)
- ❌ No audit rules (auditd) — compliance blind spot
- ❌ Building AMIs manually via console (no reproducibility, no audit trail)
- ❌ Not rotating AMIs monthly (patch accumulation)

## Related

- `container-image-apko` skill — container golden images (complementary to AMI)
- `aws-ftr-compliance` skill — CIS benchmark alignment
- `cloud-security-<org>` steering — encryption and access requirements
- `terraform-modules` skill — EC2 instance module consumes AMI via SSM
- Path: `<workspace>/01-DEVOPS/AUTOMATIONS/AMIS/`
