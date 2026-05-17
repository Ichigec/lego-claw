FROM python:3.12-alpine
WORKDIR /app
COPY relay.py .
EXPOSE 8088
ENV UPSTREAM_BASE=http://litellm:4000/v1 \
    UPSTREAM_MODEL=qwen3.6-35b-heretic \
    UPSTREAM_API_KEY=sk-local \
    LISTEN_PORT=8088
CMD ["python", "-u", "relay.py"]
