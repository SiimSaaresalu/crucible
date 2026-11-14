# Trusted evaluator only. Never use this image as a frontier agent image.
# Frozen Python 3.14.10 / Debian bookworm / Linux ARM64 base from the pilot.
FROM python@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b

LABEL org.crucible.runtime="dispatch-v2-candidate-d-v1"
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /repo
COPY requirements.txt crucible-*.whl /wheels/
RUN pip install --no-cache-dir -r /wheels/requirements.txt /wheels/crucible-*.whl
ENTRYPOINT ["python"]
