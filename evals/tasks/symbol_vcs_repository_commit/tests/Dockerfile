FROM python:3.12-slim-bookworm
WORKDIR /
COPY . /tests/
RUN chmod +x /tests/test.sh && mkdir -p /workspace /logs/verifier
CMD ["sleep", "infinity"]
