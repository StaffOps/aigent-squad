# Reference variables for the aigent-squad infra composition.
# Copy to `<cluster>.auto.tfvars` (gitignored) and fill with real values.
#
#   cp example.tfvars devops-core.auto.tfvars
#   # edit, then: terraform plan
#
# Values below are PLACEHOLDERS — replace all of them.

region                       = "us-east-1"
eks_cluster_name             = "my-cluster"
vpc_id                       = "vpc-xxxxxxxx"
private_subnet_ids           = ["subnet-aaaa", "subnet-bbbb"]
eks_worker_security_group_id = "sg-xxxxxxxx"

# Optional extra tags merged into every resource (e.g. cost allocation).
# The infra imposes no org-specific tagging scheme — leave empty or fill in.
# tags = {
#   CostCenter = "my-cost-center"
#   Environment = "PRD"
# }

# Namespace + ServiceAccount the supervisor pod actually runs as. The Helm chart
# names the SA <release>-supervisor, so the IRSA trust must target that exact SA.
k8s_namespace       = "aigent-squad"
k8s_service_account = "aigent-squad-supervisor"
