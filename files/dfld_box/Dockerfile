FROM python:3.13-alpine3.21

WORKDIR /dfld

COPY *.py ./
COPY dfld ./dfld

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

