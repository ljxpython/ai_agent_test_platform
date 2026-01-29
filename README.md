# langgraph-sql-agent-platform

这个仓库提供一个“可自托管的一体化 SQL Agent MVP 栈”：

- Nginx 作为唯一对外入口（`/` -> UI，`/api/` -> Control Plane）
- Next.js UI（支持 LangGraph streaming + Inline UI cards）
- Control Plane / BFF（FastAPI）：
  - `/api/platform/*` 平台域（审计、审批、connections）
  - `/api/lg/*` LangGraph Server API 反向代理（含 SSE 透传与 best-effort 审计写入）
  - `/api/agui/*` AG-UI SSE 网关
- LangGraph Server（`langgraph dev`）运行 SQL Agent（Chinook SQLite 只读 demo）
- Postgres 存平台元数据/审计，Redis 可选

## 文档

- 架构说明：`docs/architecture.md`
- 单机运行手册（Compose 部署 + 回滚）：`docs/runbook-single-machine.md`

## 快速开始（单机一键启动）

前置：本机 Docker Desktop/Engine 已启动，且 `80` 端口可用。

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

访问：

- UI：`http://localhost/`
- 审计页：`http://localhost/admin/audit`

冒烟：

```bash
curl -fsS -o /dev/null http://localhost/
curl -fsS -o /dev/null http://localhost/admin/audit
curl -fsS \
  -H 'X-User-Id: demo-user' \
  -H 'X-Project-Id: default' \
  http://localhost/api/platform/runs
```

## 常用命令（Makefile）

仓库根目录提供 `Makefile`：

```bash
make help
make up
make logs
make test
make e2e
make build
```

## DeepSeek 出站冒烟

脚本：`backend/scripts/deepseek_smoke.py`

环境变量支持两套命名：
- `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`
- 或者 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`（OpenAI-compatible 习惯）

```bash
set -a && source .env && set +a
backend/.venv/bin/python backend/scripts/deepseek_smoke.py
```

（兼容：如果你仍需要 Zhipu，见 `docs/runbook-single-machine.md`）
