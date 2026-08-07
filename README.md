# claude_proxy

一个把 **Claude Code** 接到 **Anthropic 兼容上游**（vLLM / GLM 等）的轻量透传代理，并附带请求/响应抓包、本地 trace 查看器。

> 当前配置的上游（见 `.env`）：`open.bigmodel.cn` 的 Anthropic 兼容接口，模型 `glm-5.2`。

---

## 目录结构

```
claude_proxy/
├── claude_proxy.py          # 代理主程序（FastAPI + httpx）
├── run_proxy.sh             # 只启动代理（常驻）
├── run_claude.sh            # 拉起代理 + 设置环境变量 + 启动 claude CLI
├── .env                     # 配置（上游地址 / API Key / 模型）⚠️ 含密钥，勿外传
├── CLAUDE.md                # 项目级 Claude 指令
├── proxy.log                # 代理运行日志
│
├── reports/                 # 抓包目录（每个请求一个 JSON）
│   ├── <session-id>/<seq>.json
│   └── unknown-session/<seq>.json   # 缺少 session-id 头时落这里
│
├── trace_viewer/            # 本地 Web 查看器（stdlib http.server）
│   ├── server.py            #   后端：读取 reports，提供 /api/traces 等
│   ├── static/              #   前端：index.html + app.js/conversation.js/subagent.js + css
│   ├── run_trace_viewer.sh  #   启动脚本
│   └── *.orig / *.log / *.pid
│
└── .claude/
    └── skills/view-traces/SKILL.md   # “打开 trace 查看器”技能定义
```

---

## 核心文件说明

### `claude_proxy.py` — 代理本体
- **框架**：FastAPI + httpx（异步）。
- **职责**：
  - 把收到的请求原样转发给上游（默认 `--direct`，不改正文）。
  - 非直连模式下会做 `sanitize_anthropic_body`：把混进 `messages[]` 里的 `system` 角色消息「提升」到顶层 `system` 字段（vLLM 要求 messages 只能是 user/assistant）。
  - `--report` 模式：把每个请求 + 重组后的响应落盘到 `reports/`。对 `text/event-stream` 流式响应会合并 SSE 事件、还原成完整 message 再写盘。
  - 写盘前对敏感头（`authorization` / `x-api-key` 等）**脱敏**。
- **关键端点**：
  - `GET /healthz` — 健康检查（返回状态、上游、模式、report 目录）。
  - `/{path:path}`（全方法）— 透传。
- **报告文件结构**：
  ```jsonc
  {
    "forwarded_request": { "method", "url", "headers", "body" },
    "response":          { "status_code", "headers", "body" }
  }
  ```

### `run_proxy.sh` — 只起代理
- 读取 `.env`，用 `uv run --with fastapi --with httpx --with uvicorn` 提供依赖。
- 默认 `127.0.0.1:30012`，开启 `--direct --report`。

### `run_claude.sh` — 一键跑通
- 先探活 `/healthz`，没起就自动拉起代理（写 `proxy.log`），就绪后才继续。
- 把 `ANTHROPIC_BASE_URL` 指向本地代理，映射好 `API_KEY → ANTHROPIC_API_KEY / AUTH_TOKEN`。
- 最后 `exec claude "$@"`，即把 `claude` CLI 的所有流量导入代理。

### `trace_viewer/` — 抓包查看器
- `bash trace_viewer/run_trace_viewer.sh` → `http://127.0.0.1:30013`。
- 后端 `server.py` 扫描 `reports/`，提供列表 / 单条 trace / usage 汇总等接口。
- 前端为原生 JS（会话视图 + 子 Agent 视图）。

### `.claude/skills/view-traces/SKILL.md`
- 让 Claude 学会：起 trace 查看器、按 session 浏览、用 `jq/python` 精确取字段而非整文件读入（trace 可能 >100KB）。

---

## 如何运行

```bash
# 方式一：只起代理（前台）
bash run_proxy.sh

# 方式二：起代理 + 跑 claude（推荐，自动管理生命周期）
bash run_claude.sh

# 单独看 trace
bash trace_viewer/run_trace_viewer.sh
# 然后浏览器打开 http://127.0.0.1:30013
```

**端口约定**

| 服务        | 默认地址            |
| ----------- | ------------------- |
| 代理        | `127.0.0.1:30012`   |
| Trace 查看器 | `127.0.0.1:30013`   |
| 上游         | 由 `.env` 指定      |

可覆盖的环境变量：`CLAUDE_PROXY_HOST/PORT/UPSTREAM/URL/LOG_LEVEL`、`TRACE_VIEWER_HOST/PORT/REPORTS`、`UV_BIN`。

---

## 备注
- `*.orig` 是改动前的备份；`*.pid` 记录查看器进程号。
- `.env` 含真实 `API_KEY`，分享/提交前注意脱敏。
- `reports/` 下是历史抓包，会持续增长，按需清理。
