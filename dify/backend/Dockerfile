FROM python:3.11-slim
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt
COPY memory_bridge.py ./
EXPOSE 19080
CMD ["python", "memory_bridge.py"]
