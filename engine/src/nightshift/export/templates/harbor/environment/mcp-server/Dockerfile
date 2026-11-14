# The simulator sidecar: the Night Shift engine in sidecar mode, installed from a vendored wheel.
FROM python:3.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PORT=8000
WORKDIR /app
COPY requirements.txt @@WHEEL_NAME@@ ./
RUN pip install --no-cache-dir -r requirements.txt ./@@WHEEL_NAME@@ \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin nightshift
USER nightshift
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn --factory nightshift.server.app:create_app --host 0.0.0.0 --port ${PORT}"]
