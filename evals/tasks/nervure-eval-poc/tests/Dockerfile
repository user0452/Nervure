FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash \
    && rm -rf /var/lib/apt/lists/*

COPY grader.py /tests/grader.py
COPY test.sh /tests/test.sh
RUN chmod +x /tests/test.sh
