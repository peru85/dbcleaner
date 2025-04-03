FROM python:3.8-slim

RUN apt-get update && \
    apt-get install -y default-mysql-client && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dbcleaner.py .
COPY s3_uploader.py .
COPY model.yml .

# Please set your variables in .env
COPY .env .

ENTRYPOINT ["python", "dbcleaner.py"]