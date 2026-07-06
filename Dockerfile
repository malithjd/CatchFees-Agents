FROM python:3.14-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY mcp_servers/ mcp_servers/
COPY web/dist/ web/dist/

# Install dependencies via uv
RUN uv sync --frozen

# Set the host and port for FastAPI
ENV HOST=0.0.0.0
ENV PORT=8080

EXPOSE 8080

ENV PYTHONPATH=src

# Command to run the FastAPI app.
# Shell form so ${PORT} expands: Cloud Run injects $PORT and the container must
# bind it; fall back to 8080 for local `docker run`.
CMD uv run uvicorn --app-dir src catchfees.server:app --host 0.0.0.0 --port ${PORT:-8080}
