FROM python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY *.py ./
ENV PYTHONUNBUFFERED=1 PC_SCOPE_FILE=/data/scope.json
EXPOSE 19080
CMD ["python", "memory_bridge.py"]
