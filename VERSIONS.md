# Updated Versions - Agent Squad v2.0

**Date**: 2026-02-14

## 🎯 LLM Model

### Claude Sonnet 3.5 v1 (Current)
```bash
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20240620-v1:0
```

**Note**: Claude Sonnet 4.5 not yet available in Bedrock. Using Claude 3.5 Sonnet v1.

**Features**:
- ✅ Excellent reasoning and analysis
- ✅ 200K token context window
- ✅ Good follow-up detection
- ✅ Accurate responses
- ✅ Low latency

---

## 📦 Python Packages (Latest)

```txt
# AWS
boto3==1.35.80                    # ✅ Updated (was 1.35.76)

# Web Framework
fastapi==0.115.8                  # ✅ Updated (was 0.115.6)
uvicorn[standard]==0.32.1         # ✅ Fixed version (0.32.2 has issues)
httpx==0.28.1                     # ✅ OK

# Cache & Storage
redis==5.2.1                      # ✅ OK

# Kubernetes
kubernetes==31.0.0                # ✅ OK

# Slack
slack-sdk==3.33.4                 # ✅ OK

# Data Validation
pydantic==2.10.5                  # ✅ Updated (was 2.10.3)
pydantic-settings==2.7.0          # ✅ OK

# Monitoring
prometheus-client==0.21.1         # ✅ OK
opentelemetry-api==1.29.0         # ✅ OK
opentelemetry-sdk==1.29.0         # ✅ OK
opentelemetry-instrumentation-fastapi==0.50b0  # ✅ OK
python-json-logger==3.2.1         # ✅ OK
```

---

## 🐳 Docker Base Images

### Python
```dockerfile
FROM python:3.12-alpine           # ✅ Latest LTS
```

**Benefits**:
- ✅ Lightweight image (~50MB vs ~900MB full)
- ✅ Security (fewer vulnerabilities)
- ✅ Fast build
- ✅ Python 3.12 (latest stable)

---

## 🔄 Changelog

### 2026-02-14 - v2.0
- ✅ Updated to Claude Sonnet 3.5 v1
- ✅ boto3: 1.35.76 → 1.35.80
- ✅ fastapi: 0.115.6 → 0.115.8
- ✅ uvicorn: fixed at 0.32.1 (0.32.2 has compatibility issues)
- ✅ pydantic: 2.10.3 → 2.10.5
- ✅ All READMEs updated

---

## 📋 Verification

### Test installed versions
```bash
pip list | grep -E "boto3|fastapi|uvicorn|pydantic"
```

### Check model ID
```bash
grep BEDROCK_MODEL_ID .env
# Should return: anthropic.claude-3-5-sonnet-20240620-v1:0
```

---

## 🚀 Next Update

Monitor:
- boto3 (updates frequently)
- fastapi (monthly releases)
- Claude models (new Anthropic releases)

**Recommended frequency**: Monthly or when security patches are released
