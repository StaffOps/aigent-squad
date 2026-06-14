FROM python:3.12-alpine

WORKDIR /app

COPY vendor/ ./vendor/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-server/mcp-server.py .

CMD ["python", "mcp-server.py"]
