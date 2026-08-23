# ---- UI build -------------------------------------------------------------
FROM node:22-slim AS ui
WORKDIR /build
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# ---- Python runtime ---------------------------------------------------------
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --extra llm
COPY src/ src/
RUN uv sync --frozen --extra llm
COPY --from=ui /build/dist /app/ui/dist
ENV TINYCLAW_GATEWAY_URL=http://gateway:9100 \
    TINYCLAW_LLM_PROVIDER=mock
EXPOSE 9100
CMD ["uv", "run", "python", "-m", "tinyclaw.gateway"]
