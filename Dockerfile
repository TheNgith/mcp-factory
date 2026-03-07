# MCP Factory – Pipeline + API container
# Runs on Linux (cross-platform analyzers only; COM/GUI/registry skipped)
FROM python:3.10-slim

# System deps: binutils for strings/nm, file for magic detection
RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    file \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pipeline dependencies first (cached layer)
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install API dependencies
COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

# Copy source
COPY src/ ./src/
COPY api/ ./api/
COPY artifacts/ ./artifacts/

# Make upload/output dirs
RUN mkdir -p /app/uploads /app/generated

# PYTHONPATH so src/discovery imports work without install
ENV PYTHONPATH=/app/src/discovery:/app/src/generation:/app/src/ui

# Tell analyzers we're in container (skips pywin32/pywinauto/COM)
ENV MCP_CONTAINER=1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
