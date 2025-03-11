FROM python:3-alpine

WORKDIR /dfld

COPY *.py ./

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

