# Idle sidecar pattern (same idea as compose.opencode.yml):
# - keep the container alive with `tail -f /dev/null`;
# - the ACP host then spawns one ACP process per request via
#   `docker exec -i <container> python3 /app/acp_agent.py`.
FROM python:3.12-slim

WORKDIR /app
COPY acp_agent.py /app/acp_agent.py

# Non-root for safety; ACP doesn't need privileges.
RUN groupadd -g 10103 acp \
 && useradd  -u 10103 -g 10103 -m -s /bin/bash acp \
 && chown -R acp:acp /app
USER acp

# Idle. Real work happens through `docker exec -i`.
CMD ["tail", "-f", "/dev/null"]
