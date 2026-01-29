# 单机运行手册（Docker Compose）

本文档描述如何在单台机器上用 Docker Compose 拉起本仓库的完整栈（Nginx 统一入口 + UI + Control Plane + LangGraph + Postgres + Redis），以及如何做基本验证与安全回滚。

约定：本文所有命令均在仓库根目录执行。

## 0. 前置条件

### 必需

- Docker Desktop / Docker Engine 已安装并启动（`docker version` 能正常返回）。
- 本机 `80` 端口可用（Compose 会把 `nginx` 映射到 `80:80`）。

### 可选（本仓库默认不对宿主机暴露）

- Postgres/Redis 端口：`5432` / `6379`。
  - 当前 `infra/docker-compose.yml` 未对宿主机开放这两个端口（仅容器内网络可达）。
  - 如果你确实需要宿主机直连，请临时在 compose 里为对应 service 增加 `ports:` 映射（属于运维改动，建议在自己的分支上做，不要无意识提交）。

## 1. 组件与端口（以配置为准）

配置来源：`infra/docker-compose.yml`、`infra/nginx/nginx.conf`。

### 服务列表

- `nginx`（唯一对外入口）
  - 宿主机端口：`http://localhost:80`
  - 路由：
    - `/` -> `ui:3000`
    - `/api/` -> `control-plane:8001`（并注入 `X-Internal-Proxy: 1`，且为 SSE 关闭 buffering）

- `ui`（Next.js）
  - 容器端口：`3000`（仅被 `nginx` 反代；compose 使用 `expose`，默认不映射到宿主机）

- `control-plane`（FastAPI / BFF）
  - 容器端口：`8001`（仅容器内可达）
  - 启动流程：先跑 Alembic migration，再 seed 默认数据，最后启动 Uvicorn。

- `langgraph`（LangGraph Server，`langgraph dev`）
  - 容器端口：`2024`（仅容器内可达）
  - 数据：只读挂载 `infra/chinook/chinook.db` 到容器内。

- `postgres`（Postgres 16）
  - 数据卷：`postgres_data`（默认持久化；回滚时必须显式处理）

- `redis`（Redis 7）

## 2. 一键启动（构建 + 后台运行）

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

常用检查：

```bash
docker compose -f infra/docker-compose.yml ps
```

提示：本 compose 未配置 healthcheck，因此 `ps` 主要用于确认容器是否处于运行态/是否频繁重启。

## 3. 查看日志与定位问题

### 查看某个服务日志

```bash
docker compose -f infra/docker-compose.yml logs -f nginx
```

也可以换成 `control-plane` / `langgraph` / `ui` / `postgres` / `redis`。

### 常见启动失败点

- `control-plane`：会在启动命令里执行 `alembic upgrade head` 和 `seed_defaults()`，任何 DB 连接/迁移错误都会导致容器反复重启。
- `postgres`：数据卷里已有旧数据时，可能与当前 migration 版本不匹配；这属于“状态问题”，需要按回滚章节处理。

## 4. 访问与冒烟验证

### 4.1 UI 是否可访问

```bash
curl -fsS -o /dev/null http://localhost/
```

如果返回非 2xx/3xx，先看 `nginx` 日志与 `docker compose ps`。

### 4.2 Control Plane 健康检查（从容器网络内验证）

Control Plane 的健康检查 endpoint 在 `backend/src/omo_platform/api/app.py`：`GET /healthz`。

宿主机默认无法直连 `control-plane:8001`（compose 未映射端口）。推荐在容器网络内验证：

```bash
docker compose -f infra/docker-compose.yml exec nginx sh -lc 'wget -qO- http://control-plane:8001/healthz'
```

说明：`nginx:alpine` 通常自带 busybox `wget`；若你的镜像变体没有，可改用 `docker compose exec control-plane python -c ...` 等方式做网络探测。

### 4.3 /api/ 反代是否生效（边界头注入）

Nginx 会把 `/api/` 转发到 Control Plane，并注入 `X-Internal-Proxy: 1`（见 `infra/nginx/nginx.conf`）。

你可以在宿主机直接打到 Nginx 来验证“通过反代访问 API”这条链路（注意平台 API 需要身份头）：

```bash
curl -fsS \
  -H 'X-User-Id: demo-user' \
  -H 'X-Project-Id: default' \
  http://localhost/api/platform/runs
```

预期：返回一个 JSON 数组（可能为空）。

注意：若你绕开 Nginx 直连 Control Plane 的 `/api/*`，而 `REQUIRE_INTERNAL_PROXY=true`，Control Plane 会拒绝（403）。这是为了强制所有外部流量都经过“单一入口”反代。

## 5. 运行测试（按仓库约定）

### 5.1 后端单测

```bash
backend/.venv/bin/python -m pytest -q
```

说明：`backend/Dockerfile` 使用 `uv sync` 管理依赖并创建 `backend/.venv`。如果你本机还没准备好 venv，需要先按项目的本地开发文档初始化依赖。

### 5.2 UI E2E（Playwright）

前置：先启动完整栈（Nginx 入口为 `http://localhost`）：

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

首次在本机安装浏览器（仅一次）：

```bash
pnpm -C ui exec playwright install chromium
```

运行 E2E：

```bash
pnpm -C ui test:e2e
```

参考：`ui/package.json`、`ui/playwright.config.ts`（默认 baseURL 指向 `http://localhost`）。

## 6. 关键环境变量与配置开关

### 6.1 compose 内部环境（生产形态默认值）

来源：`infra/docker-compose.yml`。

- `LANGGRAPH_BASE_URL`：Control Plane 访问 LangGraph Server 的上游地址（compose 内为 `http://langgraph:2024`）。
- `DATABASE_URL`：SQLAlchemy 连接串（示例：`postgresql+psycopg://postgres:postgres@postgres:5432/platform`）。
  - 代码要求该变量必须存在（见 `backend/src/omo_platform/db/session.py`）。
- `REQUIRE_INTERNAL_PROXY`：为 `true` 时，Control Plane 会要求所有 `/api/*` 请求携带 `X-Internal-Proxy: 1`（见 `backend/src/omo_platform/api/app.py`）。
- `LANGSMITH_TRACING`：LangGraph 侧 tracing 开关（compose 默认 `false`）。

### 6.2 本地开发（仓库根目录）

来源：`.env.example`。

- DeepSeek（OpenAI-compatible）：
  - `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`
  - 或 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`
  - 兼容：本仓库的本机 `.env` 可能使用小写键名（例如 `deepseek_api_key`），相关 smoke 脚本已兼容。

- `ZHIPU_API_KEY` / `ZHIPU_BASE_URL` / `ZHIPU_MODEL`（本仓库接受的命名）
- `ZHIPUAI_API_KEY` / `ZHIPUAI_API_BASE`（兼容命名；等价可用）
- `LANGSMITH_TRACING` / `LANGSMITH_API_KEY`（可选）

注意：不要把真实密钥提交到仓库。

#### Zhipu 出站冒烟（本机直接跑脚本）

脚本：`backend/scripts/zhipu_smoke.py`。

- 运行方式（从仓库根目录，显式加载 `.env`）：

```bash
set -a && source .env && set +a
backend/.venv/bin/python backend/scripts/zhipu_smoke.py
```

- 说明：脚本内部会把 `ZHIPU_API_KEY` / `ZHIPU_BASE_URL` 映射为 LangChain 侧读取的 `ZHIPUAI_API_KEY` / `ZHIPUAI_API_BASE`（`ZHIPU_MODEL` 直接使用）。
- 预期：stdout 能打印出 `pong`（流式输出）；缺少必要 env 时会打印 `SKIP: ...` 并以 0 退出。

#### DeepSeek 出站冒烟（本机直接跑脚本）

脚本：`backend/scripts/deepseek_smoke.py`。

```bash
set -a && source .env && set +a
backend/.venv/bin/python backend/scripts/deepseek_smoke.py
```

### 6.3 UI 环境变量（仅当你本机直接跑 UI 时相关）

来源：`ui/.env.example`。

- `NEXT_PUBLIC_API_URL`：UI 访问 API 的 base URL。
- `NEXT_PUBLIC_ASSISTANT_ID`：默认 assistant/graph id。

本仓库的 Compose 形态由 Nginx 统一入口提供 `/` 与 `/api/*`，因此 UI 在“通过 Nginx”访问时通常指向 `http://localhost`。

## 7. 回滚（Rollback）

回滚分三类：容器/镜像回滚、数据库回滚、整体停止回滚。请根据你要恢复的目标选择。

### 7.1 停止当前版本（不删除数据卷）

仅停止（保留容器，便于快速 `start` 恢复）：

```bash
docker compose -f infra/docker-compose.yml stop
```

停止并移除容器/网络（保留数据卷，推荐的“干净停机”）：

```bash
docker compose -f infra/docker-compose.yml down
```

说明：这会停止并删除容器与网络，但会保留 `postgres_data` 卷。

### 7.2 回滚到某个 Git 版本（推荐的“可审计回滚”）

1) 切到目标 tag/commit（示例）：

```bash
git checkout <tag-or-commit>
```

2) 重新构建并拉起：

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

注意：如果该版本的数据库 schema 与当前 `postgres_data` 内的 schema 不兼容，你仍然需要做 DB 回滚（见 7.3）。

### 7.3 数据库回滚/恢复

#### 方案 A：导出/导入（推荐，非破坏性）

导出（宿主机生成一个 SQL 备份文件）：

```bash
docker compose -f infra/docker-compose.yml exec -T postgres pg_dump -U postgres -d platform > platform.sql
```

恢复（会覆盖目标库内对象；执行前请确认你要恢复到的目标状态）：

```bash
cat platform.sql | docker compose -f infra/docker-compose.yml exec -T postgres psql -U postgres -d platform
```

#### 方案 B：快照数据卷（适合“整卷回滚”）

该仓库使用名为 `postgres_data` 的 volume 保存数据（见 `infra/docker-compose.yml`）。你可以用一个临时容器把卷打包为 tar：

```bash
mkdir -p backups
docker run --rm \
  -v postgres_data:/var/lib/postgresql/data:ro \
  -v "$PWD/backups":/backup \
  alpine \
  sh -lc 'tar -C /var/lib/postgresql/data -czf /backup/postgres_data.tgz .'
```

恢复时反向解包即可（恢复属于高风险操作，务必先停服务并确认目标卷为空/可覆盖）。

#### 方案 C：破坏性重置（仅用于开发环境）

如果你只想“干净重来”，并且明确不需要保留数据：

```bash
docker compose -f infra/docker-compose.yml down -v
```

这会删除 `postgres_data` 卷，数据不可恢复。

### 7.4 回滚镜像/依赖（不依赖 git）

如果你使用了远端镜像标签（本仓库目前是本地 build），通常通过“切 Git 版本 + build”实现回滚最稳。
若未来把镜像推送到 registry，可考虑固定 image tag、配合 `docker compose pull` 做版本回退。

## 8. 常见坑（Gotchas）

### 8.1 SSE 被 Nginx 缓冲导致“看起来卡住”

Nginx 对 `/api/` 的反代已显式关闭 buffering，并禁用 gzip（见 `infra/nginx/nginx.conf`）。如果你修改了 Nginx 配置或换了 ingress，一定要保留：

- `proxy_buffering off;`
- `proxy_set_header Connection "";`
- `proxy_read_timeout 3600s;`

### 8.2 直连 Control Plane 的 /api/* 被 403

当 `REQUIRE_INTERNAL_PROXY=true`（compose 默认）时，Control Plane 会要求 `/api/*` 必须携带 `X-Internal-Proxy: 1`（见 `backend/src/omo_platform/api/app.py`）。

这意味着：

- 正常路径：`Browser -> Nginx(/api/* 注入头) -> Control Plane`。
- 调试直连：要么临时关闭 `REQUIRE_INTERNAL_PROXY`，要么显式补齐该 header。

### 8.3 /api/* 需要身份头（尤其是 /api/platform、/api/lg、/api/agui）

Control Plane 会从请求头读取 `X-User-Id` 与 `X-Project-Id`：

- `/api/platform/*`：作为平台鉴权（`X-Project-Id` 在 MVP 中被解释为 `Project.name`；默认种子项目名为 `default`，见 `backend/src/omo_platform/db/seed.py`）。
- `/api/lg/*`：同时会把身份注入到上游 LangGraph 的 `input`（见 `backend/src/omo_platform/langgraph_proxy/router.py`）。

用 curl 调试时要记得带上：

```bash
curl -fsS \
  -H 'X-User-Id: demo-user' \
  -H 'X-Project-Id: default' \
  http://localhost/api/platform/runs
```

说明：不同 endpoint 具体路径以路由实现为准；这里的重点是“身份头是强约束”。

### 8.4 Next.js 容器启动参数的坑

`ui/Dockerfile` 明确用 `node node_modules/next/dist/bin/next start ...` 启动，避免 `pnpm` 的参数转发把 `--` 意外传给 `next start`。

### 8.5 Zhipu 出站 429 / code=1113（配额/资源包不足）

如果 `backend/scripts/zhipu_smoke.py` 的 stderr 出现类似：`PROBE: status=429 ... code=1113 ...`，通常不是代码问题，而是账号当前“资源/配额不足”。处理方式：充值，或在 Zhipu 控制台为该账号绑定/购买对应资源包后重试。
