# agentplat-sandbox (specs/02-agent-runtime.md SB-1)
#
# python:3.11-slim + pandas, matplotlib, numpy — the only packages the agent
# tools (bash/read_file/write_file/edit_file/list_files) need available for
# data-analysis style tasks (e.g. "how many rows in orders.csv").
#
# SB-2: containers built from this image run with network_mode=none,
# mem_limit=512m, nano_cpus=1e9, and as a non-root user (created below).
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pandas numpy matplotlib

# Non-root user the container always runs as (SB-2).
RUN useradd --create-home --home-dir /home/sandbox --shell /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown -R sandbox:sandbox /workspace /home/sandbox

WORKDIR /workspace
USER sandbox

CMD ["sleep", "infinity"]
