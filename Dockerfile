FROM python:3.13-slim
# uv is also needed at runtime: every generated product is its own uv project
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    STUDIO_RUNNER=local \
    STUDIO_WORKSPACES_DIR=/data/workspaces \
    CREWAI_TRACING_ENABLED=false

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY examples ./examples
RUN uv sync --frozen --no-dev

# Generated products live in /data - mount a volume to keep them between restarts
RUN mkdir -p /data/workspaces
VOLUME ["/data"]
EXPOSE 7860
# Set APP_USERNAME / APP_PASSWORD at runtime to protect the studio with a login
CMD ["uv", "run", "--frozen", "studio-ui"]
