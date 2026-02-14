# GitLab CI Setup

## 1. Create Repositories ECR

```bash
cd code/scripts
./create-ecr-repos.sh
```

Ou manual:
```bash
for repo in supervisor aws kubernetes finops devops observability; do
  aws ecr create-repository --repository-name agent-squad-$repo
done
```

## 2. Configure GitLab Variables

**Settings -> CI/CD -> Variables**

```
AWS_ACCOUNT_ID = 123456789012
AWS_REGION = us-east-1
AWS_ACCESS_KEY_ID = AKIA...
AWS_SECRET_ACCESS_KEY = *** (Protected + Masked)
```

## 3. Push for Trigger

```bash
git add .
git commit -m "Initial commit"
git push origin main
```

Pipeline builda 6 imagens em forlelo (~2min).

## 4. Check

```bash
# GitLab
CI/CD -> Pipelines

# ECR
aws ecr describe-images --repository-name agent-squad-supervisor
```

## Pipeline

- **Stages**: build
- **Triggers**: main, develop
- **Tags**: latest + commit SHA
- **Parallel**: 6 jobs simultaneous

Ver: `code/.gitlab-ci.yml`
