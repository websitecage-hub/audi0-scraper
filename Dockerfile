# Docker build for Render's free tier (the existing service uses the Docker
# runtime, and Render cannot change a service's runtime after creation).
#
# python:3.11-slim is enough: every dependency installs from a prebuilt wheel
# (see requirements.txt), so nothing compiles and no build toolchain is needed.
FROM python:3.11-slim

WORKDIR /app

# Dependencies first so Docker caches the layer across code-only pushes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    NICHE_PRIORITY=1

# Render injects PORT; shell form so the variable expands at run time.
EXPOSE 10000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --timeout-keep-alive 5"]
