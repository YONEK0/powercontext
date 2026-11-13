FROM python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
ENV PYTHONUNBUFFERED=1 POWERCONTEXT_HOME=/data/powercontext POWERCONTEXT_SERVER_WORKSPACE=/data/workspace
EXPOSE 18080
CMD ["sh", "-c", "mkdir -p /data/workspace /data/powercontext && exec powercontext server run --no-env-file --host 0.0.0.0 --port 18080"]
