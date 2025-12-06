FROM python:3.13-alpine3.21

WORKDIR /dfld

# Install system dependencies including fonts and libraries needed for luma.oled
RUN apk add --no-cache \
    ttf-dejavu

COPY *.py ./
COPY dfld ./dfld

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
