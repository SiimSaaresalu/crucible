# The agent's container. It holds no part of the simulator; the simulator runs in mcp-server.
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
    curl \
    python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
