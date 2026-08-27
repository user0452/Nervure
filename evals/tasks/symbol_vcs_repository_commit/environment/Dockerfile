FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY project/ /workspace/
RUN git init && git config user.email "benchmark@nervure.local" && git config user.name "Nervure Benchmark"     && git add . && git commit -m "buggy baseline"
CMD ["sleep", "infinity"]
