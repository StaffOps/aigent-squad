#!/bin/bash
set -e

echo "🚀 Agent Squad v2.0 - Local Setup"
echo ""

# Check prerequisites
echo "📋 Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose not found. Please install Docker Compose."
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo "⚠️  AWS CLI not found. Install for easier setup."
fi

echo "✅ Prerequisites OK"
echo ""

# Setup environment
if [ ! -f .env.local ]; then
    echo "📝 Creating .env.local from example..."
    cp .env.example .env.local
    echo ""
    echo "⚠️  IMPORTANT: Edit .env.local with your credentials:"
    echo "   - AWS credentials (for Bedrock access)"
    echo "   - GITLAB_TOKEN (read-only token for Company)"
    echo "   - SLACK_BOT_TOKEN (optional, for Slack integration)"
    echo ""
    echo "   vim .env.local"
    echo ""
    read -p "Press Enter when ready..."
fi

# Load environment
export $(grep -v '^#' .env.local | xargs)

# Check AWS credentials
echo "🔑 Checking AWS credentials..."
if [ ! -d "$HOME/.aws" ]; then
    echo "❌ AWS credentials not found in ~/.aws/"
    echo "   Run: aws configure"
    exit 1
fi

echo "✅ AWS credentials found"
echo ""

# Check GitLab token
if [ -z "$GITLAB_TOKEN" ]; then
    echo "⚠️  GITLAB_TOKEN not set in .env.local"
    echo "   DevOps Agent will have limited functionality"
    echo ""
fi

# Start Redis
echo "🗄️  Starting Redis..."
docker-compose up -d redis
sleep 3

# Start DynamoDB Local
echo "🗄️  Starting DynamoDB Local..."
docker-compose up -d dynamodb-local
sleep 3

# Create DynamoDB table with correct schema
echo "📊 Creating DynamoDB table (pk + sk schema)..."
if command -v aws &> /dev/null; then
    aws dynamodb create-table \
        --table-name agent-sessions \
        --attribute-definitions \
            AttributeName=pk,AttributeType=S \
            AttributeName=sk,AttributeType=S \
        --key-schema \
            AttributeName=pk,KeyType=HASH \
            AttributeName=sk,KeyType=RANGE \
        --billing-mode PAY_PER_REQUEST \
        --endpoint-url http://localhost:8100 \
        2>/dev/null && echo "   ✅ Table created" || echo "   ℹ️  Table already exists"
else
    echo "   ⚠️  AWS CLI not found, skipping table creation"
fi

echo ""

# Build images
echo "🏗️  Building Docker images (this may take a few minutes)..."
docker-compose build --parallel

echo ""

# Start all agents
echo "🚀 Starting all agents..."
docker-compose up -d

echo ""
echo "⏳ Waiting for services to be healthy (30s)..."
sleep 30

# Check health
echo ""
echo "🏥 Health check:"
services=(
    "8000:Supervisor"
    "8001:AWS Agent"
    "8002:Kubernetes Agent"
    "8003:FinOps Agent"
    "8004:DevOps Agent"
    "8005:Observability Agent"
)

all_healthy=true
for service in "${services[@]}"; do
    port="${service%%:*}"
    name="${service##*:}"
    if curl -s http://localhost:$port/health > /dev/null 2>&1; then
        echo "   ✅ $name (port $port)"
    else
        echo "   ❌ $name (port $port) - NOT HEALTHY"
        all_healthy=false
    fi
done

echo ""

if [ "$all_healthy" = true ]; then
    echo "✨ Setup complete! All services are healthy."
else
    echo "⚠️  Some services are not healthy. Check logs:"
    echo "   docker-compose logs -f"
fi

echo ""
echo "📚 Quick Start:"
echo ""
echo "   # Test AWS Agent"
echo "   curl -X POST http://localhost:8001/process \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"input_text\":\"List EC2 instances\",\"user_id\":\"test\",\"session_id\":\"test123\",\"chat_history\":[]}'"
echo ""
echo "   # Test Supervisor (with Classifier)"
echo "   curl -X POST http://localhost:8000/query \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"user_input\":\"Quantas instâncias EC2?\",\"user_id\":\"test\",\"session_id\":\"test123\"}'"
echo ""
echo "   # View logs"
echo "   docker-compose logs -f supervisor"
echo "   docker-compose logs -f aws-agent"
echo ""
echo "   # Restart agent after changes"
echo "   docker-compose restart aws-agent"
echo ""
echo "   # Stop all"
echo "   docker-compose down"
echo ""
echo "📖 Full documentation: docs/LOCAL_DEVELOPMENT.md"
echo ""
