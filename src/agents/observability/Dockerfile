FROM python:3.12-alpine

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Expose port (will be overridden by each agent)
EXPOSE 8005

# Run server (will be overridden by each agent)
CMD ["python", "-m", "src.agents.observability.server"]
