FROM ghcr.io/astral-sh/uv:python3.13-trixie

COPY ./lightning/pyproject.toml /lightning/pyproject.toml
COPY ./lightning/uv.lock /lightning/uv.lock
WORKDIR /lightning
RUN uv sync --frozen --no-install-project -i https://mirrors.aliyun.com/pypi/simple/

COPY ./lightning /lightning
RUN uv sync --frozen -i https://mirrors.aliyun.com/pypi/simple/

ENV TZ=Asia/Shanghai