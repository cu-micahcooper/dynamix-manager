FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir '.[hosted]' \
    && groupadd --gid 10001 connector \
    && useradd --uid 10001 --gid 10001 --create-home connector \
    && mkdir /data && chmod 755 /data
# The launcher initializes only /data/private, then execs as UID/GID 10001.
EXPOSE 8000
CMD ["python", "/app/src/dynamix_manager/hosted_start.py"]
