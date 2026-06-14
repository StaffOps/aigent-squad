FROM python:3.12-alpine

WORKDIR /app

COPY vendor/ ./vendor/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8002
CMD ["python", "-m", "src.agents.kubernetes.server"]
