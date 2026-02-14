FROM python:3.12-alpine

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy MCP server
COPY mcp-server/mcp-server.py .

# Make executable
RUN chmod +x mcp-server.py

# Run MCP server
CMD ["python", "mcp-server.py"]
