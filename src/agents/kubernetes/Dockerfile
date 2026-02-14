FROM python:3.12-alpine

WORKDIR /app

# Install AWS CLI for EKS authentication
RUN apk add --no-cache aws-cli

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Expose port
EXPOSE 8002

# Run server
CMD ["python", "-m", "src.agents.kubernetes.server"]
