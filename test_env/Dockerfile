# syntax=docker/dockerfile:1.7-labs

FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

WORKDIR /workspace

CMD ["python3", "test_env/run_policy_hook_tests.py", "--work-root", "/workspace/.tmp"]
