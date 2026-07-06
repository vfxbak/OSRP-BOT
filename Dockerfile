FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONWARNINGS=default

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["sh", "-c", "echo '=== CONTAINER STARTED ===' && python -u main.py"]
