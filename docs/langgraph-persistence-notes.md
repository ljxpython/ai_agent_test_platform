# LangGraph 持久化与 Redis（非 Docker）调研笔记

> 结论先行：`langgraph dev` 目前是**强制 in-memory** 的开发模式，无法通过 `.env`/`langgraph.json` 配置为 Postgres/Redis。
> 
> 若要“非 Docker 的持久化 + Redis”，要么：
> 1) 不用 LangGraph Server，平台自研 execution service（推荐，且与你们的 Control Plane 目标一致）；
> 2) 不用 `langgraph dev`，改为直接启动 `langgraph_api.server:app`，并确保安装了对应 runtime backend（但本仓库环境当前仅有 inmem backend）。

## 1. `langgraph dev` 为什么无法配置持久化

在当前环境中，`langgraph dev` 会走 `langgraph_api/cli.py:run_server()`，并在启动前强制 patch 环境变量：

- `MIGRATIONS_PATH="__inmem"`
- `DATABASE_URI=":memory:"`
- `REDIS_URI="fake"`

证据：`backend/.venv/lib/python3.12/site-packages/langgraph_api/cli.py` 内 `to_patch = dict(...)`。

并且它会把 `.env` 读入的环境变量合并进去，但对 `to_patch` 里已有的 key 会 **skip overwrite**（因此 `.env` 中的 `DATABASE_URI/REDIS_URI` 不会生效）。

结论：`langgraph dev` 适合快速开发/调试，不适合作为“带持久化/Redis 的本地仿真生产”。

## 2. LangGraph Server 的持久化能力依赖什么

LangGraph Server（`langgraph-api`）通过 `langgraph_runtime` 选择不同 backend。

`langgraph_runtime/__init__.py` 逻辑（本地可读）：
- 通过 env `LANGGRAPH_RUNTIME_EDITION` 选择 runtime 包名：`langgraph_runtime_${edition}`
- 若对应包不存在会直接报错：提示 `pip install "langgraph-runtime-${edition}"`

本仓库当前可见的 runtime backend：
- `langgraph_runtime_inmem` ✅ 存在（`backend/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/`）
- `langgraph_runtime_postgres` ❌ 不存在
- `langgraph_runtime_community` ❌ 不存在

因此，即便不用 `langgraph dev`，在当前 Python 环境里也**无法**直接切换到 postgres edition。

## 3. 方案 A（推荐）：平台自研 execution service（不依赖 langgraph dev/server）

思路：
- Control Plane 作为唯一入口，对外提供稳定的 `/api/v1/agents/*`。
- 内部直接调用 `langgraph` 的 `graph.compile(checkpointer=...)`，使用 Postgres checkpointer 来实现持久化。
- Redis 由平台自行使用（队列、限流、断流恢复索引等），不依赖 `langgraph dev` 的 fake redis。

优点：
- 不被 LangGraph Server API 版本牵引；更符合企业“稳定契约”的诉求。
- 你们想做的审批/审计/策略，全在 Control Plane 一处实现，执行面只跑图。

## 4. 方案 B（存在但受限）：不用 `langgraph dev`，直接跑 LangGraph Server + 持久化

理论启动方式：
- 用 `uvicorn langgraph_api.server:app` 启动服务
- 用环境变量配置：`DATABASE_URI`、`REDIS_URI`、`LANGGRAPH_RUNTIME_EDITION` 等（见 `langgraph_api/config.py`）

现实限制：
- 需要安装并可用的 runtime backend 包（例如 `langgraph_runtime_postgres`），否则 `langgraph_runtime` 会在 import 阶段失败。
- 当前仓库 venv 中未找到 `langgraph_runtime_postgres`，因此本地无法验证/运行。

## 5. 对当前项目的直接影响（你们现在要做的事）

- 既然目标架构已确定为“Control Plane 唯一入口 + 稳定 API”，推荐把“持久化 + Redis”优先落到你们的 execution service（方案 A），而不是继续纠结 `langgraph dev` 的持久化。
- Docker/Compose 可以后置：当你们 API/执行面稳定后，再用 Compose 做“可复制验收环境”即可。
