FROM python:3.11.17-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy requirements file(s) first for better layer caching
COPY requirements.txt .

# Install dependencies (--no-cache-dir keeps pip's download cache out of the image).
# setuptools and wheel only matter for building packages, and their vendored
# copies carry known CVEs, so remove them once the install is done.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y setuptools wheel

# Run as an unprivileged user. The app writes chat history to /app/data when
# MongoDB is unreachable, so that folder (and only that folder) must be writable.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data/conversations \
    && chown -R appuser:appuser /app/data

# Copy the rest of the application code (read-only for the app user)
COPY . .

USER 10001

EXPOSE 8000

# The slim image has no curl, so probe GET / with Python's stdlib. It runs inside
# the container, so 127.0.0.1 is correct here. A non-2xx answer or a refused
# connection raises, which makes the command (and the check) fail.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
