FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# 依赖单独成一层：只改代码时不重装包（本仓的 langchain/pandas 那一层重装一次要几分钟）。
# 清单只有 pyproject.toml 与包本体需要先进来——`[tool.setuptools.packages.find]` 只收 codeharness*，
# 所以不 COPY server/ 也能装；装完再放运行时代码。
COPY pyproject.toml ./
COPY codeharness/ ./codeharness/
RUN pip install ".[server]"

# 运行时代码：`uvicorn server.app:app` 从 WORKDIR 解析 `server` 与 `platforms`（它们不是已安装的包）
COPY server/ ./server/
COPY platforms/ ./platforms/

# 可写落点：workspace_root 默认 ./workspace（`settings.py:144`），断点库在它下面的 storage/；
# `server/app.py:35` 会 mkdir，但**权限**要在这里定好——下面换成非 root 用户跑。
# compose 那份 `workspace:/app/workspace` 是命名卷，首次挂载会把这里的属主带进卷。
RUN mkdir -p /app/workspace/storage /app/server/data \
    && useradd --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
