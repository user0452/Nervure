FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY calculator /workspace/calculator
COPY visible_tests /workspace/visible_tests

# The task workspace needs a baseline so the agent-side git_diff.patch is a
# meaningful artifact rather than an always-empty best-effort command.
RUN git init -q \
    && git config user.email "harbor@example.invalid" \
    && git config user.name "Harbor Fixture" \
    && git add calculator visible_tests \
    && git commit -qm "fixture baseline"
