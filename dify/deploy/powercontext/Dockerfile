FROM python:3.11-slim
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir -i ${PIP_INDEX} "powercontext[server,client]==1.4.1" typer
ENV HOME=/data
VOLUME ["/data"]
EXPOSE 18080
CMD ["powercontext", "server", "run", "--host", "0.0.0.0", "--port", "18080"]
