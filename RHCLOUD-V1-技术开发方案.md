---
title: RHCLOUD V1 技术开发方案
version: 1.0
updated: 2026-06-27
status: Draft for implementation
---

# RHCLOUD V1 技术开发方案

> 配置驱动的多模型接力对话流水线（Multi-Model Conversation Pipeline）的工程实施方案。本文在原《RHCLOUD V1 完整技术与落地实施方案》（架构宪法 + 代码骨架）之上，补全可执行的需求规格、模块详细设计、API 契约、数据模型、测试/部署/安全/可观测性方案，并修正原设计中若干会在生产环境直接导致失败的工程缺陷。

---

## 0. 文档说明

### 0.1 适用范围
本文是面向开发执行的技术方案，目标是让一名（或一个小团队）工程师据此即可落地 V1 MVP。读者为：实现工程师、Code Review 者、部署/运维者。

### 0.2 与源文档的关系
源文档（下称"原方案"）确立了产品定位与四条架构宪法，并给出代码骨架。本文继承其全部设计意图（配置驱动、文件即数据库、极简 Runtime），但在落地层面做两类工作：

- **补全**：原方案缺失的需求规格、非功能指标、API 契约、错误码、数据 Schema、测试、安全、可观测性、运维。
- **修正**：原方案的代码骨架存在若干在生产环境必然出问题的缺陷（见第 5 节）。每处修正均标注 `[修正-N]`，给出问题、原因、对策，便于与原方案对照评审。

本文不改动四条架构宪法。所有修正都在"宪法允许的范围内"做工程加固，不引入数据库、不引入 DAG、不引入 Agent。

### 0.3 术语表
| 术语 | 全称 / 定义 |
|------|------------|
| Provider | 一个"模型网站 + 账号实例"的抽象，对应 `providers.yaml` 中一个条目 |
| Site | 模型网站类型（chatgpt / claude / grok / qwen），对应 `src/providers/{site}.py` 一个文件 |
| Profile | 单个账号的 Playwright 持久化状态（Cookie / storage_state / user-data-dir）目录 |
| Step | 流水线中的一个接力节点，含 `key` / `provider` / `prompt` 三字段 |
| Pipeline | 一条由若干 Step 组成的线性流水线（一个 YAML 文件） |
| Context | 一次运行的内存状态，含 `user_question` 与各 Step 输出 |
| Session | 一次完整运行，以 UUID 标识，对应 `data/sessions/{uuid}/` 一个目录 |
| Job | 一次异步执行任务的运行时实体（含状态机），是 Session 在运行期的载体 |
| Session Seeding | 在本地有头浏览器登录后导出账号会话、注入部署环境的过程 |

---

## 1. 项目概述

### 1.1 产品定位
RHCLOUD V1 是一个配置驱动的多模型接力对话流水线。它把多个 AI 按固定线性顺序串起来，围绕同一个问题接力思考、逐轮优化答案。它不是 Agent、不是 Workflow 引擎、不是 AI OS。

用户交互恒为三步：输入问题 → 等待流水线跑完 → 决定"满意导出"或"再来一轮"。

### 1.2 V1 目标（可度量）
1. 能以纯配置（YAML + Markdown）定义一条 ≥5 步的接力流水线，新增模型/账号/调序均不改 Runtime 代码。
2. 能稳定驱动至少 3 个模型网站（建议首批：ChatGPT、Claude、Qwen）完成一轮完整接力。
3. 单次运行的每一步 Prompt 与 Response 均落盘为独立 Markdown，可在 IDE 中直接审阅。
4. 前端能实时（流式）看到每一步的执行状态与产出，并支持"再来一轮"。
5. 单次运行端到端成功率（在会话有效、无验证码拦截的正常态下）≥ 90%。

### 1.3 范围边界
V1 **做**：线性接力、配置驱动、文件存储、单实例部署、流式前端、基础容错。

V1 **坚决不做**（继承原方案"不做清单"，作为范围红线）：抽象基类/接口继承、手动 import Provider、`driver/enabled/type` 等冗余字段、复杂状态机、noVNC/人工接管、自动断点恢复、SQL 数据库、DAG/并行/Merge、Agent/意图识别/自动评分、RAG/知识库/插件。

> 范围纪律：任何"为了可能的未来"的能力一律推迟到 V2（见第 16 节演进路线），不在 V1 提前支付复杂度。

---

## 2. 关键前置决策：网页自动化 vs 官方 API

这是开工前必须明确拍板的根本性技术分叉，直接决定后续 80% 的工程量与稳定性。原方案默认走"网页自动化（Playwright）"，本文如实陈列两条路线的事实成本，供决策。

### 2.1 事实对比
| 维度 | 网页自动化（Playwright 驱动网页版） | 官方 API（OpenAI / Anthropic / xAI / 阿里云 DashScope） |
|------|-----------------------------------|------------------------------------------------------|
| 稳定性 | 低。依赖 DOM/接口结构，对方改版即失效 | 高。接口有版本与契约保障 |
| 反爬/封禁风险 | 高。Headless 易被识别，触发验证码、限流、封号 | 无。API 即官方支持的接入方式 |
| 登录维护 | 需定期 Session Seeding，会话过期需人工重登 | 一次性配置 API Key |
| 计费模型 | 复用订阅额度（Plus/Pro 包月，边际成本近零） | 按 Token 计费，规模化后成本显著 |
| 合规（ToS） | 多数厂商网页版 ToS 禁止自动化，存在账号被封风险 | 合规 |
| 可访问模型 | 可用部分"仅网页提供、无 API"的模型/形态 | 仅限官方开放 API 的模型 |
| 工程量 | 高（每个网站独立适配 + 反爬对抗 + 会话运维） | 低（统一 HTTP 调用） |

### 2.2 合规与账号风险（如实提示）
对 ChatGPT / Claude / Grok / Qwen 等网页版进行自动化操作，通常违反各厂商服务条款，存在账号被限流或封禁的现实风险。本方案按你的需求实现网页自动化路径，但将该风险列为**最高级别风险**（见第 15 节 R-01），并强烈建议：凡是官方提供 API 的模型，优先走 API；仅对"无 API、必须用网页形态"的模型才用 Playwright。

### 2.3 本文建议：Provider 抽象同时兼容两条路线
原方案的 `manager.py` 动态加载机制天然支持这一点——`run(profile, prompt)` 是唯一契约，其内部既可以是 Playwright，也可以是一次 API 调用。建议：

- `src/providers/chatgpt.py`（网页自动化） 与 `src/providers/openai_api.py`（官方 API） 可并存为两个 Site。
- `providers.yaml` 中按需引用：成本敏感、模型有 API 的走 `*_api`；必须网页形态的走网页 Site。
- Runtime / Builder / Manager 一行不改即可混跑两类 Provider。

> 决策点（需拍板）：V1 首批 3 个模型，哪些走 API、哪些走网页？建议至少把其中 1 个走 API，作为"稳定基线"，避免三条链路同时受反爬影响导致整条流水线不可用。

本文后续详细设计**同时覆盖**两类 Provider，但把工程难点集中在网页自动化路径（API 路径是其平凡子集）。

---

## 3. 需求规格

### 3.1 功能需求（FR）
| 编号 | 需求 | 说明 |
|------|------|------|
| FR-01 | 配置化流水线 | 通过 `pipelines/*.yaml` 定义线性 Step 序列；Step 三字段 `key/provider/prompt` |
| FR-02 | 配置化账号 | 通过 `config/providers.yaml` 定义 Provider 实例（site + profile，及可选运行参数） |
| FR-03 | 配置化 Prompt | `prompts/*.md` 模板，支持 `{{user_question}}` 与 `{{<step_key>}}` 引用历史步骤输出 |
| FR-04 | 动态 Provider 路由 | Manager 按 `site` 动态加载 `src/providers/{site}.py`，调用 `run()`，新增模型不改 Manager |
| FR-05 | 线性接力执行 | Runtime 顺序执行每个 Step：构建 Prompt → 调用 Provider → 落盘 → 写入 Context |
| FR-06 | 流式输出 | 每步完成实时推送前端（含 step key、provider、状态、内容） |
| FR-07 | 全程落盘 | 每步的 Prompt 与 Response 各存为独立 `.md`；运行结束落盘 `context.json` |
| FR-08 | 再来一轮 | 加载 `continue.yaml`，携带上一轮 Context 继续优化 |
| FR-09 | 导出 | 用户可导出最终结果（至少导出 summary 步骤的 Markdown / 整个 Session） |
| FR-10 | 错误中断 | 某步失败时按策略重试；超过重试上限则中断流水线并明确告知失败步骤与原因 |
| FR-11 | 会话注入 | 提供本地 Session Seeding 流程，把账号登录态注入部署环境 |

### 3.2 非功能需求（NFR，带可度量目标）
| 编号 | 类别 | 目标 |
|------|------|------|
| NFR-01 | 性能-单步 | 在会话有效的正常态下，单步（Prompt 提交→提取结果）P50 ≤ 60s，P95 ≤ 120s（受模型生成时长支配，以"生成完成判定"为准） |
| NFR-02 | 性能-冷启动 | 复用浏览器上下文，避免每步冷启动；同一 Profile 连续步骤复用同一 Context |
| NFR-03 | 可靠性 | 正常态端到端成功率 ≥ 90%；瞬时导航错误自动重试（默认 2 次，指数退避） |
| NFR-04 | 可恢复性 | SSE 连接中断后，前端可重连并从断点续读事件（基于事件日志 + Last-Event-ID） |
| NFR-05 | 并发安全 | 同一 Profile 在任意时刻仅被一个 Step 占用（Profile 级互斥锁） |
| NFR-06 | 安全 | `/jobs` 等写接口必须鉴权；CORS 限定前端域名；账号会话文件按机密管理，不入 Git |
| NFR-07 | 可观测 | 结构化日志（JSON）覆盖每步开始/结束/错误；暴露 `/health` 与基础运行指标 |
| NFR-08 | 可维护 | 每个网站的适配逻辑封装在单文件内，网站改版只改单文件，不波及核心 |
| NFR-09 | 数据留存 | Session 落盘可配置保留期（默认 30 天），超期清理（含敏感内容） |
| NFR-10 | 资源 | 单实例下并发运行的 Job 数受限（默认 ≤ 2，可配置），避免内存/浏览器进程耗尽 |

---

## 4. 总体架构

### 4.1 组件视图
```text
                          User (Browser)
                                │  HTTPS
                                ▼
                   [ Cloudflare Pages ]  Next.js 前端
                                │  HTTPS (REST + SSE)，带鉴权头
                                ▼
                   [ Railway ]  FastAPI 后端（单实例）
        ┌───────────────────────┼───────────────────────────┐
        ▼                       ▼                           ▼
   API 层 (main.py)        执行引擎 (runtime.py)        会话存储 (data/)
   - POST /jobs            - 后台 Worker（线程/进程）   - profiles/ (登录态, Volume)
   - SSE  /jobs/{id}/events- 事件日志 + 队列            - sessions/ (落盘, Volume)
   - GET  /jobs/{id}       - StepResult 状态机
   - 鉴权 / 限流 / CORS     - Profile 级互斥锁
                                │
                                ▼
                        Provider 层
              builder.py（安全模板替换）
              manager.py（按 site 动态加载）
              src/providers/{site}.py  ── run(profile, prompt) -> str
                    ├── 网页自动化：Playwright（持久上下文 + 反检测 + 网络拦截）
                    └── 官方 API：HTTP 调用（可选并存）
```

### 4.2 数据流（一次运行）
1. 前端 `POST /jobs`（含 user_question、pipeline、鉴权头）→ 后端创建 Job（UUID）、建 Session 目录、入队，立即返回 `job_id`。
2. 后台 Worker 取出 Job，按 Pipeline 顺序执行每个 Step：`builder.build()` → `manager.run()` → 落盘 → 写 Context → 追加事件到事件日志。
3. 前端 `GET /jobs/{id}/events`（SSE）订阅，实时收到每步事件；断线后凭 `Last-Event-ID` 重连续读。
4. 全部 Step 完成或中断 → 落盘 `context.json`，Job 置终态（`succeeded` / `failed`）。
5. 用户选择"导出"或"再来一轮"（后者用 `continue.yaml` + 上一轮 Context 发起新 Job）。

### 4.3 对原架构的关键修正（汇总）
| 编号 | 原方案 | 问题 | 本文对策 |
|------|--------|------|----------|
| 修正-1 | `sync_playwright` 直接在 FastAPI SSE 生成器中运行 | 同步 Playwright 不能跑在 asyncio 事件循环里，运行即抛错或阻塞事件循环 | 执行与请求解耦：后台 Worker 线程跑同步 Playwright，事件经队列/日志流给 SSE |
| 修正-2 | 单条 SSE 连接贯穿整轮（可能 5–15 分钟） | 长连接易被代理超时/缓冲、移动网络断开，无法续传 | Job 化：先返回 job_id；SSE 从事件日志回放，支持 Last-Event-ID 重连；提供轮询兜底 |
| 修正-3 | Builder 末尾 `re.sub(r'\{\{.*?\}\}','')` 清空所有未匹配占位符 | 会误删模型输出里合法的 `{{ }}`（如 Vue/Jinja 代码），污染下游引用 | 白名单替换，仅替换已知变量，不做兜底全清；未知占位符原样保留 |
| 修正-4 | 出错以 `"[ERROR: ...]"` 字符串混入 outputs，靠 `startswith` 判断 | 错误与正常文本混淆、不可结构化、无重试 | 结构化 StepResult（status/error/attempt），错误单独落盘，分类重试 |
| 修正-5 | `headless=True` 直连 ChatGPT/Claude | 主流站点反爬，Headless 易被识别/拦截，登录态无法应对验证码 | 持久上下文 + 反检测（stealth/指纹）+ 有头(Xvfb) + 网络拦截 + Session Seeding |
| 修正-6 | 每步 `launch → close` 新建浏览器 | 每步冷启动，5 步串行总时长不可接受 | 同一 Profile 复用浏览器上下文（上下文池），跨步保活 |
| 修正-7 | `allow_origins=["*"]`、`/run` 无鉴权 | 任何人可调用，烧光付费账号额度 | 写接口强制鉴权 + 限流；CORS 限定前端域名 |
| 修正-8 | 多 Session 共用同一 Profile 无并发控制 | storage_state 写竞争 + 同账号并行会话冲突 | Profile 级互斥锁，竞争同一 Profile 的 Step 串行化 |

---

## 5. 关键技术风险与架构对策（详述）

本节是本方案相对原方案的核心增量。原方案的代码骨架在"思路演示"层面成立，但若直接部署，下列问题会逐一爆发。

### 5.1 [修正-1] 同步 Playwright 与异步 SSE 的冲突
**问题**：原 `main.py` 在 `def run()` 里用生成器驱动 `run_pipeline`，而 `run_pipeline` 内部 `manager.run()` 最终调用 `sync_playwright()`。Playwright 同步 API 一旦检测到当前线程有运行中的 asyncio 事件循环，会直接抛 `Error: It looks like you are using Playwright Sync API inside the asyncio loop`。

**对策**：执行与 HTTP 请求彻底解耦。
- 用一个**后台 Worker**（V1 用独立线程池即可）运行整条 Pipeline；Worker 线程内没有 asyncio 事件循环，可安全运行同步 Playwright。
- Worker 每完成一步，把事件**追加写入事件日志文件**（`events.jsonl`）并推入内存队列。
- SSE 端点是一个轻量异步生成器：订阅该 Job 的事件队列 + 回放事件日志，把事件转发给前端。它**不碰 Playwright**。

（备选：全栈改用 `async_playwright` + 全异步 Runtime。但这要求 provider 也全异步，改造面更大；V1 推荐"同步 Worker + 异步 SSE 转发"，改动最小、最稳。）

### 5.2 [修正-2] 长任务下的 SSE 可靠性
**问题**：一轮 5 步、每步可能 1–2 分钟，单条 SSE 连接需存活 5–15 分钟。经 Cloudflare → Railway 的链路上，空闲超时、反向代理缓冲、移动端断网都会让连接中途死亡，且原设计无法续传——连接一断，用户就丢失后续所有步骤。

**对策**：Job 化 + 可重连 SSE。
- `POST /jobs` 立即返回 `job_id`，执行在后台。
- `GET /jobs/{id}/events` 为 SSE，**带 `id:` 字段**（事件序号）；每个事件先持久化到 `events.jsonl` 再下发。
- 客户端断线重连时携带 `Last-Event-ID`，服务端从该序号之后**回放**，保证不丢事件。
- 每 15s 发送一条 SSE 注释心跳（`: keepalive\n\n`）防止中间层判定空闲。
- 同时提供 `GET /jobs/{id}` 轮询兜底（返回当前状态 + 已完成步骤摘要），用于 SSE 完全不可用的环境。

### 5.3 [修正-3] Prompt Builder 的占位符污染
**问题**：原 Builder 第三步 `template = re.sub(r'\{\{.*?\}\}', '', template)` 会清掉"所有"双花括号片段。但模型的输出常含合法 `{{ }}`（Jinja、Vue、Handlebars 代码示例，甚至 LaTeX）。当这种输出被下游步骤通过 `{{key}}` 引用拼进新 Prompt 时，其中的 `{{ }}` 会被无差别删除，造成内容损坏且难以排查。

**对策**：改为白名单替换，不做兜底全清（详见 6.2）。仅替换"本次 Context 中确实存在的变量"（`user_question` 与已产生的 step key）；对未知 `{{...}}` 一律**原样保留**。这样既满足"引用历史步骤"的需求，又不会破坏模型输出里的合法花括号。

### 5.4 [修正-4] 错误处理：从字符串哨兵到结构化结果
**问题**：原方案把异常转成 `"[ERROR: ...]"` 字符串写进 `outputs`，再靠 `response_text.startswith("[ERROR")` 判断中断。缺陷：错误文本与正常内容同构、无法分类、无重试、若某步正常输出恰以 `[ERROR` 开头会误判。

**对策**：引入结构化 `StepResult`（详见 6.5）：
```text
StepResult = {
  key, provider, prompt_name,
  status: "succeeded" | "failed",
  attempt: int,
  content: str | null,
  error: { type, message } | null,
  started_at, finished_at
}
```
- 错误分类决定重试策略：瞬时类（导航失败、网络抖动、生成超时）默认重试 2 次、指数退避；致命类（会话失效/被要求登录、验证码、选择器彻底失配）**快速失败**并提示"需重新 Seeding 会话"。
- 错误内容单独落盘 `NN_{key}_error.json`，不污染正常 Response。

### 5.5 [修正-5] 反检测与会话注入（网页自动化的真正难点）
**问题**：`chromium.launch(headless=True)` 访问 ChatGPT/Claude/Grok 会被反爬体系（Cloudflare Turnstile、指纹检测、`navigator.webdriver` 等）识别，轻则弹验证码，重则直接拦截。且首登需要邮箱/2FA/验证码，Headless 无法完成。

**对策**：
1. **持久上下文**：用 `launch_persistent_context(user_data_dir=...)` 而非"无痕 + storage_state"，让浏览器像真实用户那样积累状态，降低被判异常的概率。
2. **反检测**：注入反指纹脚本（如 `playwright-stealth` 或自定义 `init_script` 抹掉 `navigator.webdriver`、伪装 plugins/WebGL 等）；设置真实 UA、语言、时区、视口。
3. **有头运行**：在容器内用 `Xvfb` 提供虚拟显示，以**有头模式**运行 Chromium（有头比无头更不易被判定为 bot）。Dockerfile 增加 `xvfb` 并用 `xvfb-run` 启动。
4. **Session Seeding 流程**（FR-11）：在本地有头浏览器手动登录目标账号 → 导出 `user_data_dir`/`storage_state` → 作为机密注入 Railway Volume。会话会过期，需把"重新 Seeding"列为常规运维动作（见第 10、12 节）。
5. **网络拦截优先于 DOM 提取**：监听 `page.on("response")`，从模型后端接口（如 `backend-api/conversation`）直接取结构化文本，绕开 DOM 改版（原方案"坑 3"思路正确，本文将其设为各 Provider 的首选实现路径，DOM 提取作为兜底）。

> 现实预期：即便全部到位，网页自动化仍是持续的猫鼠对抗，维护成本高。这正是第 2 节建议"至少一条链路走官方 API"的原因。

### 5.6 [修正-6] 浏览器复用，消除每步冷启动
**问题**：原 Provider 每次 `run()` 都 `launch → ... → close`。5 步串行，每次冷启动浏览器 + 加载页面 + 等待网络空闲，叠加后端总时长不可接受，且频繁起停进程也更易触发风控。

**对策**：上下文池。按 Profile 维护一个长生命周期的 `BrowserContext`：
- 首次使用某 Profile 时创建并保活；后续步骤复用同一 Context（同账号的连续步骤无需重登、无需重开页面）。
- Job 结束或空闲超时后回收。
- 与 5.8 的 Profile 锁配合：同一 Profile 的 Context 同一时刻只服务一个 Step。

### 5.7 [修正-7] 接口鉴权、CORS 与限流
**问题**：原 `main.py` `allow_origins=["*"]` 且 `/run` 无任何鉴权。部署后任何人都能调用、烧光所有付费账号额度，并可借你的账号产生不可控内容。

**对策**：
- **鉴权**：所有写接口（`POST /jobs`）要求 `Authorization: Bearer <token>`，token 由环境变量配置（V1 用共享密钥即可，V1.x 升级为签名/短时令牌）。
- **CORS**：`allow_origins` 限定为前端域名（Cloudflare Pages 域），不用 `*`。
- **限流**：按 token / IP 做基础速率限制（如每分钟 N 次 `POST /jobs`、最大并发 Job 数，见 NFR-10）。

### 5.8 [修正-8] Profile 并发与文件竞争
**问题**：同一 Profile 被两个并发 Session 同时使用时：一是 `storage_state`/`user_data_dir` 写竞争损坏会话；二是同一网页账号无法干净地并行持有两个对话。

**对策**：**Profile 级互斥锁**。Runtime 调用某 Provider 前，先按其 `profile` 路径获取锁；同一 Profile 的 Step 串行化执行。V1 单实例用进程内 `threading.Lock`（按 profile 建锁字典）即可；多实例（V2）再升级为文件锁/Redis 锁。

---

## 6. 详细设计

### 6.1 配置层

#### 6.1.1 账号配置 `config/providers.yaml`
继承原方案的极简风格，但允许**可选**运行参数（不破坏"极简"——未配置时用全局默认，配置了才生效）。

```yaml
# 全局默认（可选；未写则用代码内置默认）
defaults:
  timeout_ms: 120000        # 单步生成等待上限
  retries: 2                # 瞬时错误重试次数
  retry_backoff_ms: 3000    # 退避基数（指数）

providers:
  chatgpt_web_1:
    site: chatgpt            # 必填：对应 src/providers/chatgpt.py
    profile: data/profiles/chatgpt_acc1   # 必填：登录态目录
    # timeout_ms: 180000    # 可选：覆盖该实例的等待上限

  claude_web_1:
    site: claude
    profile: data/profiles/claude_acc1

  qwen_web:
    site: qwen
    profile: data/profiles/qwen_acc1

  # 可选：官方 API 路径与网页路径并存
  openai_api_1:
    site: openai_api
    profile: ""             # API 路径不需要 profile
    # API Key 通过环境变量注入，不写入 YAML（见第 9 节）
```

字段规范：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `site` | string | 是 | — | 决定加载哪个 `src/providers/{site}.py` |
| `profile` | string | 是 | — | 登录态目录路径；API 类 Provider 允许空串 |
| `timeout_ms` | int | 否 | defaults | 该实例单步等待上限 |
| `retries` | int | 否 | defaults | 该实例瞬时错误重试次数 |

> 扩展方式不变：新增账号加几行 YAML；新增网站只新建 `src/providers/{site}.py`。Manager/Runtime/Builder 不改。

#### 6.1.2 流水线配置 `pipelines/*.yaml`
Step 仍为三字段 `key / provider / prompt`，保持原方案契约。`round1.yaml`（含 generate）与 `continue.yaml`（跳过 generate）结构同原方案。

```yaml
name: "QA Refinement Round 1"
steps:
  - key: generate
    provider: chatgpt_web_1
    prompt: generate
  - key: grok_review
    provider: qwen_web        # 首批若不上 Grok，可替换为已上线 Provider
    prompt: review
  - key: claude_deep
    provider: claude_web_1
    prompt: deep_analyze
  - key: improve
    provider: chatgpt_web_1
    prompt: improve
  - key: summary
    provider: qwen_web
    prompt: summary
```

校验规则（启动时与提交 Job 时各校验一次，失败即拒绝并明确报错）：
- 每个 Step 的 `provider` 必须存在于 `providers.yaml`。
- 每个 Step 的 `prompt` 对应的 `prompts/{prompt}.md` 必须存在。
- `key` 在同一 Pipeline 内唯一。
- Prompt 模板内引用的 `{{<key>}}` 必须是"在它之前出现过的 Step key"或 `{{user_question}}`（前向引用检查，避免引用尚未产生的输出——这是 V1 易踩的逻辑错）。

#### 6.1.3 Prompt 模板 `prompts/*.md`
仅两类变量：`{{user_question}}` 与 `{{<step_key>}}`。模板内容、写法同原方案。系统强制提醒段（重申原始需求、防跑题）保留。

### 6.2 Prompt Builder（`src/builder.py`，含 [修正-3]）
白名单替换，不做兜底全清。

```python
import os

class PromptBuilder:
    def __init__(self, prompts_dir: str = "prompts"):
        self.prompts_dir = prompts_dir

    def build(self, prompt_name: str, context: dict) -> str:
        """
        将 prompts/{prompt_name}.md 中的 {{user_question}} 与 {{<step_key>}}
        替换为 context 中的对应值。仅替换白名单内的已知变量；
        未知的 {{...}} 一律原样保留，避免破坏模型输出里的合法花括号。
        """
        prompt_path = os.path.join(self.prompts_dir, f"{prompt_name}.md")
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()

        # 白名单：user_question + 已产生的所有 step 输出
        variables = {"user_question": context.get("user_question", "")}
        variables.update(context.get("outputs", {}))

        # 仅替换确实存在的变量；不存在的占位符不动
        for name, value in variables.items():
            template = template.replace("{{" + name + "}}", str(value))

        return template
```

要点：去掉了原方案的 `re.sub(r'\{\{.*?\}\}','')`。若某 `{{key}}` 因配置错误未被替换，它会**原样保留**在最终 Prompt 中——这反而成为一个显式的故障信号（在落盘的 `_prompt.md` 里一眼可见），优于"静默清空"。配置校验（6.1.2）已在更早阶段拦截大多数此类错误。

### 6.3 Provider Manager（`src/manager.py`）
保持原方案的动态加载契约，补充：实例参数解析、Provider 模块缓存、清晰错误。

```python
import importlib
import yaml

DEFAULTS = {"timeout_ms": 120000, "retries": 2, "retry_backoff_ms": 3000}

class ProviderManager:
    def __init__(self, config_path: str = "config/providers.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.defaults = {**DEFAULTS, **(cfg.get("defaults") or {})}
        self.providers = cfg["providers"]
        self._module_cache = {}

    def resolve(self, provider_name: str) -> dict:
        conf = self.providers.get(provider_name)
        if not conf:
            raise ValueError(
                f"Provider '{provider_name}' not found in providers.yaml")
        return {
            "site": conf["site"],
            "profile": conf.get("profile", ""),
            "timeout_ms": conf.get("timeout_ms", self.defaults["timeout_ms"]),
            "retries": conf.get("retries", self.defaults["retries"]),
            "retry_backoff_ms": self.defaults["retry_backoff_ms"],
        }

    def _load(self, site: str):
        if site not in self._module_cache:
            self._module_cache[site] = importlib.import_module(
                f"src.providers.{site}")
        return self._module_cache[site]

    def run(self, provider_name: str, prompt: str) -> str:
        conf = self.resolve(provider_name)
        module = self._load(conf["site"])
        # 契约：每个 provider 必须实现 run(profile, prompt, **options) -> str
        return module.run(
            conf["profile"], prompt,
            timeout_ms=conf["timeout_ms"],
        )
```

契约（不变 + 明确化）：每个 `src/providers/{site}.py` 必须实现 `run(profile, prompt, **options) -> str`，返回模型输出的纯文本（Markdown）。无抽象基类、无接口继承——函数签名即契约。

### 6.4 Provider 实现规范（网页自动化，含 [修正-5][修正-6][修正-8]）
以 `src/providers/chatgpt.py` 为参照说明实现要点（其余网站同构，差异封装在各自文件内）。

实现清单（每个网页 Provider 必须做到）：
1. **持久上下文 + 复用**：通过共享的浏览器上下文管理器按 `profile` 获取/复用 `BrowserContext`，不每步新建浏览器。
2. **反检测**：注入 stealth init_script、设置真实 UA/语言/时区/视口。
3. **网络拦截优先**：`page.on("response")` 捕获后端接口响应，解析出纯文本；DOM 提取仅作兜底。
4. **生成完成判定**：用明确信号判定（如"停止生成"按钮消失 / "重新生成"按钮出现 / 接口流结束），**禁用 `time.sleep()`**，统一用 `wait_for`。
5. **会话失效检测**：若被重定向到登录页或出现登录控件，抛 `SessionExpiredError`（致命类，触发快速失败 + 重新 Seeding 提示）。
6. **Profile 锁**：由上层（Runtime / 上下文管理器）保证同一 profile 串行；Provider 内不并发复用同一 Context。

参考骨架（要点示意，省略具体选择器——选择器需按各站点当时 DOM/接口实测）：

```python
from src.providers._browser import get_context   # 共享上下文池（按 profile 复用）
from src.providers._errors import SessionExpiredError, GenerationTimeout

CHATGPT_URL = "https://chat.openai.com"

def run(profile: str, prompt: str, timeout_ms: int = 120000, **_) -> str:
    ctx = get_context(profile)          # 复用持久上下文（[修正-6]）
    page = ctx.new_page()
    captured = {"text": None}

    def on_response(resp):
        # 网络拦截优先（[修正-5]）：从后端接口取结构化文本
        if "backend-api/conversation" in resp.url:
            captured["text"] = _parse_stream(resp)

    page.on("response", on_response)
    try:
        page.goto(CHATGPT_URL, wait_until="domcontentloaded")
        if _is_login_page(page):        # 会话失效检测
            raise SessionExpiredError(profile)

        editor = page.locator('div[contenteditable="true"]')
        editor.click()
        page.keyboard.type(prompt, delay=10)   # 模拟人类输入
        page.keyboard.press("Enter")

        # 生成完成判定（禁用 sleep；以信号为准）
        _wait_generation_done(page, timeout_ms)

        text = captured["text"] or _extract_last_dom(page)  # DOM 兜底
        if not text:
            raise GenerationTimeout(profile)
        return text
    finally:
        page.close()                    # 关页不关上下文（上下文池保活）
```

> 说明：`_browser.py`（上下文池 + 反检测）、`_errors.py`（错误类型）为内部辅助模块，被各 Provider 复用，但**不构成抽象基类**——它们是工具函数，不是继承体系，符合"约定大于配置"。

官方 API Provider（如 `src/providers/openai_api.py`）则是上述的平凡子集：

```python
import os, requests

def run(profile: str, prompt: str, timeout_ms: int = 120000, **_) -> str:
    # API Key 从环境变量取，不入 YAML
    key = os.environ["OPENAI_API_KEY"]
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout_ms / 1000,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

### 6.5 执行引擎（`src/runtime.py`，含 [修正-1][修正-4][修正-8]）
Runtime 仍是"纯接力 For 循环 + 无业务判断"，但加固为：结构化结果、分类重试、Profile 锁、事件落盘。它运行在后台 Worker 线程中（非事件循环线程），可安全调用同步 Playwright。

```python
import json, os, time, threading, datetime
from src.builder import PromptBuilder
from src.manager import ProviderManager
from src.providers._errors import SessionExpiredError

_profile_locks = {}
_locks_guard = threading.Lock()

def _lock_for(profile: str) -> threading.Lock:
    with _locks_guard:
        if profile not in _profile_locks:
            _profile_locks[profile] = threading.Lock()
        return _profile_locks[profile]

def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"

def run_pipeline(pipeline_path, user_question, session_dir, emit):
    """
    执行一条流水线。emit(event: dict) 由上层注入，
    负责把事件持久化到 events.jsonl 并推送给 SSE。
    """
    with open(pipeline_path, "r", encoding="utf-8") as f:
        import yaml; config = yaml.safe_load(f)

    builder = PromptBuilder()
    manager = ProviderManager()
    context = {"user_question": user_question, "outputs": {}}
    os.makedirs(session_dir, exist_ok=True)
    seq = 0
    def push(ev):
        nonlocal seq
        seq += 1
        ev["seq"] = seq
        emit(ev)

    for index, step in enumerate(config["steps"]):
        key, provider_name, prompt_name = step["key"], step["provider"], step["prompt"]
        prompt_text = builder.build(prompt_name, context)
        prefix = f"{index + 1:02d}_{key}"
        _write(f"{session_dir}/{prefix}_prompt.md", prompt_text)
        push({"type": "step_started", "key": key, "provider": provider_name})

        conf = manager.resolve(provider_name)
        result = _run_step_with_retry(
            manager, provider_name, prompt_text, conf, key)

        if result["status"] == "succeeded":
            context["outputs"][key] = result["content"]
            _write(f"{session_dir}/{prefix}_response.md", result["content"])
            push({"type": "step_succeeded", "key": key,
                  "provider": provider_name, "content": result["content"]})
        else:
            _write_json(f"{session_dir}/{prefix}_error.json", result["error"])
            push({"type": "step_failed", "key": key,
                  "provider": provider_name, "error": result["error"]})
            # 致命错误中断整条流水线（继承原方案"出错即中断"）
            break

    _write_json(f"{session_dir}/context.json", context)
    push({"type": "pipeline_finished"})
    return context

def _run_step_with_retry(manager, provider_name, prompt, conf, key):
    attempt = 0
    started = _now()
    while True:
        attempt += 1
        try:
            lock = _lock_for(conf["profile"])      # [修正-8] Profile 锁
            with lock:
                content = manager.run(provider_name, prompt)
            return {"status": "succeeded", "content": content,
                    "attempt": attempt, "started_at": started,
                    "finished_at": _now(), "error": None}
        except SessionExpiredError as e:
            # 致命类：不重试，提示重新 Seeding
            return _fail("session_expired", str(e), attempt, started)
        except Exception as e:
            if attempt > conf["retries"]:           # 瞬时类：超过上限才失败
                return _fail("transient", str(e), attempt, started)
            time.sleep(conf["retry_backoff_ms"] / 1000 * (2 ** (attempt - 1)))

def _fail(etype, msg, attempt, started):
    return {"status": "failed", "content": None, "attempt": attempt,
            "started_at": started, "finished_at": _now(),
            "error": {"type": etype, "message": msg}}

def _write(path, text):
    with open(path, "w", encoding="utf-8") as f: f.write(text)

def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
```

### 6.6 API 层（`src/main.py`，含 [修正-1][修正-2][修正-7]）
Job 化 + 后台 Worker + 可重连 SSE + 鉴权/CORS/限流。

```python
import json, os, uuid, queue, threading
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor
from src.runtime import run_pipeline

app = FastAPI(title="RHCLOUD V1")

FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "https://your-frontend.pages.dev")
AUTH_TOKEN = os.environ["RHCLOUD_AUTH_TOKEN"]
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],          # [修正-7] 不用 "*"
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT)
_jobs = {}          # job_id -> {status, queue, events:list, session_dir}
_jobs_guard = threading.Lock()

def _require_auth(authorization: str):
    if authorization != f"Bearer {AUTH_TOKEN}":     # [修正-7] 鉴权
        raise HTTPException(status_code=401, detail="unauthorized")

@app.post("/jobs")
async def create_job(request: Request, authorization: str = Header(None)):
    _require_auth(authorization)
    body = await request.json()
    user_question = body["user_question"]
    pipeline = body.get("pipeline", "pipelines/round1.yaml")

    job_id = str(uuid.uuid4())
    session_dir = f"data/sessions/{job_id}"
    q = queue.Queue()
    with _jobs_guard:
        _jobs[job_id] = {"status": "running", "queue": q,
                         "events": [], "session_dir": session_dir}

    def emit(ev):
        with open(f"{session_dir}/events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        _jobs[job_id]["events"].append(ev)
        q.put(ev)

    def worker():                                   # [修正-1] 后台线程跑同步 Playwright
        os.makedirs(session_dir, exist_ok=True)
        try:
            run_pipeline(pipeline, user_question, session_dir, emit)
            _jobs[job_id]["status"] = "succeeded"
        except Exception as e:
            emit({"type": "fatal", "error": str(e)})
            _jobs[job_id]["status"] = "failed"
        finally:
            q.put(None)                             # 结束哨兵

    _executor.submit(worker)
    return {"job_id": job_id}

@app.get("/jobs/{job_id}")
async def get_job(job_id: str, authorization: str = Header(None)):
    _require_auth(authorization)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"job_id": job_id, "status": job["status"], "events": job["events"]}

@app.get("/jobs/{job_id}/events")
async def stream_events(job_id: str, request: Request,
                        last_event_id: str = Header(None)):   # [修正-2] 可重连
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")

    async def gen():
        start = int(last_event_id) if last_event_id else 0
        for ev in job["events"]:                  # 回放断点之后
            if ev.get("seq", 0) > start:
                yield _sse(ev)
        if job["status"] == "running":            # 继续实时推送
            q = job["queue"]
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"        # 心跳
                    continue
                if ev is None:
                    break
                if ev.get("seq", 0) > start:
                    yield _sse(ev)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})

def _sse(ev: dict) -> str:
    return f"id: {ev.get('seq','')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"

@app.get("/health")
async def health():
    return {"status": "ok"}
```

> 注：`/jobs` 上的鉴权放在 SSE 端点之外，是因为浏览器原生 `EventSource` 不能自定义请求头；SSE 鉴权在 V1 可用"短时一次性 token 作为查询参数"或改用 `fetch` + ReadableStream 方案（见 6.7）。这是 SSE 的已知约束，需在前端实现时一并处理。

### 6.7 前端（Next.js on Cloudflare Pages）
组件与状态机（最小集）：

- 输入区：问题输入框 + Pipeline 选择（round1 / continue）+ 提交按钮。
- 执行区：按 Step 顺序展示节点卡片，状态 `pending → running → succeeded/failed`，成功后内联渲染该步 Markdown 输出。
- 结果区：流水线结束后展示 summary，提供"导出 Markdown / 导出整个 Session"与"再来一轮"。

数据流：
1. 提交 → `POST /jobs`（带 `Authorization` 头）→ 得 `job_id`。
2. 订阅事件：因原生 `EventSource` 无法带鉴权头，V1 推荐用 `fetch` + `ReadableStream` 手动解析 SSE（可带 `Authorization` 头，亦便于处理 `Last-Event-ID` 重连）。
3. 断线 → 用记录的最后 `seq` 作为 `Last-Event-ID` 头重新 `fetch`，从断点续读。
4. 完全失败兜底 → 轮询 `GET /jobs/{id}`。

"再来一轮"：把上一轮 `context.json`（或其 summary）作为新一轮的输入上下文，`POST /jobs` 时指定 `pipeline=pipelines/continue.yaml`。Runtime 不感知"第几轮"——由前端决定加载哪个 Pipeline（继承原方案设计）。

---

## 7. 数据模型与存储（文件即数据库）

### 7.1 Session 目录结构
```text
data/sessions/{job_id}/
├── context.json                 # 最终上下文（见 7.2）
├── events.jsonl                 # 事件日志，每行一个事件（见 7.3），SSE 回放的来源
├── 01_generate_prompt.md        # 实际发送的完整 Prompt
├── 01_generate_response.md      # 模型返回的完整 Markdown
├── 02_grok_review_prompt.md
├── 02_grok_review_response.md
├── 03_claude_deep_prompt.md
├── 03_claude_deep_response.md
├── ...
└── NN_{key}_error.json          # 仅失败步骤产生（见 7.4）
```

设计价值（继承原方案）：零成本 Debug（IDE 直接打开对应 `_prompt.md`/`_response.md`）、无 ORM/SQL、完全可追溯。

### 7.2 `context.json` Schema
```json
{
  "user_question": "字符串：原始问题",
  "outputs": {
    "generate": "字符串：generate 步骤的完整输出",
    "grok_review": "字符串：review 步骤的完整输出",
    "claude_deep": "字符串：deep_analyze 步骤的完整输出"
  }
}
```

### 7.3 `events.jsonl` 事件 Schema（每行一个 JSON）
| 字段 | 类型 | 说明 |
|------|------|------|
| `seq` | int | 事件序号，单调递增，用于 SSE `Last-Event-ID` 续传 |
| `type` | string | `step_started` / `step_succeeded` / `step_failed` / `pipeline_finished` / `fatal` |
| `key` | string | Step key（生命周期类事件可缺省） |
| `provider` | string | Provider 实例名 |
| `content` | string | 仅 `step_succeeded`：该步输出 |
| `error` | object | 仅 `step_failed` / `fatal`：`{type, message}` |

### 7.4 错误文件 `NN_{key}_error.json`
```json
{ "type": "transient | session_expired", "message": "错误描述" }
```

### 7.5 登录态存储 `data/profiles/`
每个账号一个目录（`user_data_dir` 或 `storage_state.json`）。属机密数据：不入 Git；以 Railway Volume 持久化；目录权限收紧（见第 9 节）。

### 7.6 并发与一致性
- 同一 Profile 的写竞争由 Profile 锁（5.8）消除。
- `events.jsonl` 仅由该 Job 的单个 Worker 追加写，无跨 Job 竞争。
- Session 目录以 `job_id` 隔离，天然无冲突。

### 7.7 留存与清理（NFR-09）
后台定时任务（或启动时扫描）删除超过保留期（默认 30 天，环境变量可配）的 Session 目录，避免磁盘膨胀与敏感内容长期滞留。

---

## 8. API 契约

### 8.1 端点总览
| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/jobs` | 是 | 创建并启动一次运行，返回 `job_id` |
| GET | `/jobs/{job_id}` | 是 | 查询 Job 状态与已发生事件（轮询兜底） |
| GET | `/jobs/{job_id}/events` | 是* | SSE 实时事件流，支持 `Last-Event-ID` 续传 |
| GET | `/health` | 否 | 健康检查 |

\* SSE 鉴权受浏览器 `EventSource` 限制，见 6.6 / 6.7 的处理方式。

### 8.2 `POST /jobs`
请求：
```json
{
  "user_question": "帮我设计一个高并发短链服务",
  "pipeline": "pipelines/round1.yaml"
}
```
请求头：`Authorization: Bearer <token>`，`Content-Type: application/json`

响应（200）：
```json
{ "job_id": "8f7e9d2a-1b3c-4d5e-9a0b-1c2d3e4f5a6b" }
```

错误：401（鉴权失败）、400（缺字段 / Pipeline 校验失败）、429（超并发/限流）。

### 8.3 `GET /jobs/{job_id}`
响应（200）：
```json
{
  "job_id": "8f7e9d2a-...",
  "status": "running",
  "events": [
    { "seq": 1, "type": "step_started", "key": "generate", "provider": "chatgpt_web_1" },
    { "seq": 2, "type": "step_succeeded", "key": "generate", "provider": "chatgpt_web_1", "content": "..." }
  ]
}
```
错误：401、404（job 不存在）。

### 8.4 `GET /jobs/{job_id}/events`（SSE）
事件帧示例：
```text
id: 2
data: {"seq":2,"type":"step_succeeded","key":"generate","provider":"chatgpt_web_1","content":"..."}

: keepalive

event: done
data: {}
```
重连：客户端带 `Last-Event-ID: 2`，服务端只回放 `seq > 2` 的事件。

### 8.5 错误码表
| HTTP | 含义 | 触发条件 |
|------|------|----------|
| 400 | 请求非法 | 缺 `user_question`；Pipeline/Provider/Prompt 校验失败 |
| 401 | 未授权 | 缺失或错误的 `Authorization` |
| 404 | 资源不存在 | `job_id` 未找到 |
| 429 | 限流 | 超过最大并发 Job 数或速率限制 |
| 500 | 服务端错误 | Worker 未捕获异常（同时以 `fatal` 事件下发） |

业务层步骤失败不体现为 HTTP 错误码，而是以 `step_failed` / `fatal` 事件经事件流传达，并落盘 `error.json`。

---

## 9. 安全与合规

### 9.1 鉴权与访问控制（[修正-7]）
- 写接口（`POST /jobs`）强制 `Bearer` token；token 经环境变量注入，绝不写入代码/配置文件/Git。
- CORS 限定前端域名；不使用 `*`。
- V1.x 演进：把共享密钥升级为带过期的签名令牌；SSE 用一次性短时 token。

### 9.2 限流（NFR-10）
- 最大并发 Job 数（默认 2，环境变量可配），由线程池 `max_workers` 与显式计数共同约束。
- 按 token/IP 的速率限制（每分钟创建 Job 上限），防止额度被刷爆。

### 9.3 机密管理
| 机密 | 存储 | 注入方式 |
|------|------|----------|
| 账号登录态（profiles） | Railway Volume，目录权限 700 | Session Seeding 注入，不入 Git |
| 官方 API Key | 环境变量 | Railway Secrets |
| 后端鉴权 token | 环境变量 | Railway Secrets |

`.gitignore` 必须包含 `data/profiles/`、`data/sessions/`、`.env`。

### 9.4 ToS 与法务（R-01，最高级风险）
对模型网页版进行自动化通常违反其服务条款，存在账号被限流或封禁的现实风险。建议：
- 凡有官方 API 的模型，优先走 API（合规且稳定）。
- 仅对"无 API、必须用网页形态"的模型用 Playwright，并将账号视为可随时损失的资源（多账号、可快速 Seeding 替换）。
- 不要在该系统上承载任何对外正式业务承诺，直至明确各厂商条款边界。

### 9.5 数据隐私
- 用户问题与模型输出可能含敏感信息；落盘内容受 7.7 留存期约束，超期物理删除。
- 部署区域、日志中避免打印完整 Prompt/Response 正文（仅打印 metadata，见 10.1）。

---

## 10. 可观测性与运维

### 10.1 日志（NFR-07）
结构化 JSON 日志，每条含：`ts`、`job_id`、`step_key`、`provider`、`event`（started/succeeded/failed/retry）、`attempt`、`duration_ms`、`error_type`。**不打印** Prompt/Response 正文（隐私 + 体积），正文以落盘文件为准。

### 10.2 指标（最小集）
| 指标 | 说明 |
|------|------|
| `jobs_total` / `jobs_failed_total` | 运行与失败总数 |
| `step_duration_seconds`（按 provider/site 分桶） | 单步耗时分布，用于核对 NFR-01 |
| `step_retries_total` | 重试次数，反映链路稳定性 |
| `session_expired_total`（按 provider） | 会话失效频率，触发 Seeding 节奏 |
| `active_jobs` | 当前并发 Job 数，对照 NFR-10 |

V1 可用日志聚合得到上述指标；无需引入完整监控栈。

### 10.3 健康检查与告警
- `/health` 供 Railway 探活。
- 当某 Provider 连续 N 次 `session_expired` 或 `transient`，输出显著日志（V1 用日志告警；V1.x 接 Webhook/飞书通知）。

### 10.4 常规运维动作
| 动作 | 触发 | 操作 |
|------|------|------|
| 会话 Seeding | `session_expired` 升高 / 首次部署 | 本地有头登录 → 导出 profile → 注入 Volume |
| 选择器/接口修复 | 某 site 步骤稳定失败、`transient` 飙升 | 更新对应 `src/providers/{site}.py` |
| Session 清理 | 周期 | 自动任务按留存期删除（NFR-09） |
| 账号轮换 | 某账号被风控 | `providers.yaml` 切换到备用实例 |

---

## 11. 测试策略

| 层级 | 范围 | 方式 | 门禁 |
|------|------|------|------|
| 单元测试 | `builder.py`（含 [修正-3] 用例：输出含 `{{ }}` 不被破坏）、`manager.resolve`、配置校验、重试与错误分类 | pytest，无外部依赖 | CI 必过 |
| 契约测试 | 每个 `providers/{site}.py` 暴露 `run(profile, prompt, **opts)->str` 且返回非空文本 | 用录制的接口响应回放（mock `page.on("response")`），不打真实网站 | CI 必过 |
| 集成测试 | Runtime 串起 Builder + Manager + 桩 Provider，验证落盘文件、事件序列、Profile 锁串行化 | pytest + 桩 site | CI 必过 |
| API 测试 | `/jobs` 鉴权、Pipeline 校验、SSE 事件帧与 `Last-Event-ID` 续传、轮询兜底 | httpx + 桩 Runtime | CI 必过 |
| E2E 冒烟 | 真实驱动 1 个网页 Provider + 1 个 API Provider 跑通最短 Pipeline | 人工/定时，独立环境 | 发布前手动 |

关键测试用例（必须覆盖）：
- Builder：模板含 `{{user_question}}`、`{{generate}}` 正常替换；模型输出含 `{{ vue_var }}` 透传不删除；未配置的 `{{xxx}}` 原样保留。
- 重试：瞬时错误重试到上限后置 `failed`；`SessionExpiredError` 不重试、直接 `failed`。
- 并发：两个 Job 引用同一 Profile 时 Step 串行（锁生效）。
- SSE：断开后带 `Last-Event-ID` 重连，不重复、不丢失事件。

---

## 12. 部署与环境

### 12.1 拓扑
```text
        [ GitHub Repo ]  (代码 / YAML / Prompt 模板，不含机密)
              │
   ┌──────────┴───────────┐
   ▼                      ▼
[ Cloudflare Pages ]   [ Railway ]
 前端 Next.js           后端 FastAPI + Playwright（单实例）
 - 全球 CDN / SSL        - Chromium + Xvfb 运行环境
 - 自动 CI/CD            - Volume 挂载 data/（profiles + sessions）
```

### 12.2 Dockerfile（含 [修正-5] 的 Xvfb 有头支持）
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 有头运行所需的虚拟显示
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN playwright install chromium      # 预装，避免首次运行超时

EXPOSE 8000
# 用 xvfb-run 提供虚拟显示，使 Chromium 可有头运行
CMD ["xvfb-run", "-a", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 12.3 `requirements.txt`
```text
fastapi
uvicorn
pyyaml
playwright
playwright-stealth
requests
```

### 12.4 环境变量
| 变量 | 必填 | 说明 |
|------|------|------|
| `RHCLOUD_AUTH_TOKEN` | 是 | 后端写接口鉴权 token |
| `FRONTEND_ORIGIN` | 是 | 允许的前端域名（CORS） |
| `MAX_CONCURRENT_JOBS` | 否 | 最大并发 Job 数，默认 2 |
| `SESSION_RETENTION_DAYS` | 否 | Session 留存天数，默认 30 |
| `OPENAI_API_KEY` 等 | 视情况 | 启用对应 API Provider 时配置 |

### 12.5 Railway 配置要点
- 挂载 Volume 到 `data/`，确保 `profiles/`（登录态）与 `sessions/`（落盘）跨重启持久。
- 配置 `/health` 探活。
- 单实例运行（V1）：Profile 进程内锁仅在单实例内有效；不要横向扩容到多实例，否则锁失效（V2 再上分布式锁）。

### 12.6 Session Seeding 流程（FR-11，落地步骤）
1. 本地以有头模式运行对应 Provider 的登录脚本，打开目标网站，手动完成登录/2FA/验证码。
2. 导出该账号的 `user_data_dir`（或 `storage_state.json`）。
3. 将导出物作为机密上传到 Railway Volume 对应 `data/profiles/{acc}/`，权限收紧。
4. 触发一次冒烟，确认该 Provider 可正常驱动。
5. 记录会话有效期经验值，安排周期性重新 Seeding。

### 12.7 CI/CD
- GitHub Push → Cloudflare Pages 自动构建前端；Railway 自动构建后端镜像并部署。
- CI 流水线：lint → 单元/契约/集成/API 测试 → 构建镜像。机密通过平台 Secrets 注入，不进仓库。

---

## 13. 实施计划

> 时间为单人投入的工程估计。**网页自动化的反爬对抗耗时高度不可控**（DOM/接口随时变、风控随时升级），故对相关阶段保留缓冲，并把"至少一条 API 链路"作为可交付保底。

### 阶段一：基建与核心链路（约 5 个工作日）
| 序 | 任务 | 交付物 / 验收 |
|----|------|--------------|
| 1 | 初始化 Monorepo + CI/CD（GitHub + Cloudflare Pages + Railway） | 自动部署跑通，`/health` 可访问 |
| 2 | Dockerfile（含 Xvfb）+ Railway 有头 Chromium | 容器内能有头打开网页并截图 |
| 3 | `builder.py`（[修正-3]）+ `manager.py` + 配置校验 + 单元测试 | 读 YAML、白名单替换、动态加载、校验全过；CI 绿 |
| 4 | API 路径 Provider（如 `openai_api.py`）作为稳定基线 | 单步：构建 Prompt → 调 API → 返回文本 |
| 5 | `runtime.py`（[修正-1][修正-4][修正-8]）跑通单节点 | 读 YAML → 构建 → 调 Provider → 结构化结果 → 落盘 MD/JSON |

阶段一 DoD：仅用 API Provider，即可跑通一条最短 Pipeline 并完整落盘；核心代码层（builder/manager/runtime）测试通过。此时系统"骨架已活"，不依赖任何网页自动化。

### 阶段二：网页 Provider 与多模型闭环（约 5 个工作日 + 缓冲）
| 序 | 任务 | 交付物 / 验收 |
|----|------|--------------|
| 6 | `_browser.py`（持久上下文池 + 反检测，[修正-5][修正-6]）+ `_errors.py` | 单 Provider 复用上下文、注入 stealth，能稳定提交并提取一次结果 |
| 7 | 死磕首个网页 Provider（建议 ChatGPT 或 Qwen）：拦截优先 + 生成判定 + 会话失效检测 | 网络拦截取文本跑通；`SessionExpiredError` 可触发 |
| 8 | 补全第二/第三个网页 Provider | 多 site 契约测试通过 |
| 9 | 编写 `round1.yaml` + `continue.yaml` + 5 个 Prompt 模板 | 端到端跑通 5 步接力（混用 API + 网页） |
| 10 | Session Seeding 流程文档化 + 实操注入 | 真实账号会话注入后冒烟通过 |

阶段二 DoD：一条 ≥3 模型的混合 Pipeline 端到端跑通，每步落盘可审阅，会话可注入。

### 阶段三：API 加固、前端与可交付（约 5 个工作日）
| 序 | 任务 | 交付物 / 验收 |
|----|------|--------------|
| 11 | `/jobs` Job 化 + 后台 Worker + 鉴权/CORS/限流（[修正-2][修正-7]） | API 测试通过；越权被拒；超并发返回 429 |
| 12 | 可重连 SSE（`Last-Event-ID` 续传 + 心跳）+ 轮询兜底 | 断线重连不丢/不重事件 |
| 13 | Next.js 前端：输入 → 提交 → SSE 渲染节点状态 → 内联输出 | 用户可见每步实时执行 |
| 14 | "再来一轮"（加载 `continue.yaml` + 上一轮 Context）+ 导出 | 多轮优化闭环 + 可导出 |
| 15 | 可观测性（结构化日志/指标）、Session 清理任务、异常打磨 | NFR-07/09 达标；V1 MVP 可交付 |

阶段三 DoD：见第 14 节验收标准。

---

## 14. 验收标准（V1 Definition of Done）

- [ ] 纯配置定义并跑通一条 ≥5 步线性流水线；新增账号仅改 `providers.yaml`，新增网站仅加 `src/providers/{site}.py`，Runtime/Builder/Manager 零改动。
- [ ] 至少 3 个模型（建议含 ≥1 个 API Provider）能完成一轮完整接力。
- [ ] 每步 Prompt 与 Response 各落盘为独立 `.md`，失败步骤落盘 `error.json`，运行结束落盘 `context.json` 与 `events.jsonl`。
- [ ] 前端实时展示每步状态与产出；SSE 断线可重连续读；提供轮询兜底。
- [ ] "再来一轮"可携带上一轮 Context 继续优化；可导出最终结果。
- [ ] 写接口强制鉴权、CORS 限定前端域、超并发返回 429。
- [ ] Builder 不破坏模型输出中的合法 `{{ }}`；瞬时错误重试、会话失效快速失败并提示重新 Seeding。
- [ ] 同一 Profile 的并发步骤串行化（Profile 锁验证通过）。
- [ ] 单元/契约/集成/API 测试在 CI 全过；E2E 冒烟人工通过。
- [ ] 正常态端到端成功率 ≥ 90%（连续 N 次冒烟统计）。

---

## 15. 风险登记表

| 编号 | 风险 | 可能性 | 影响 | 对策 |
|------|------|--------|------|------|
| R-01 | 网页自动化违反厂商 ToS，账号被限流/封禁 | 高 | 高 | 优先 API；网页账号视为可损耗资源、多账号备份、可快速 Seeding；不承载对外正式业务 |
| R-02 | 反爬升级导致网页 Provider 失效 | 高 | 高 | 网络拦截优先 + 单文件隔离 + 保留 API 保底链路；监控 `transient`/`session_expired` 指标 |
| R-03 | 会话频繁过期，Seeding 运维负担重 | 中 | 中 | 持久上下文降低失效频率；文档化 Seeding；监控失效频率定节奏 |
| R-04 | 长任务下 SSE 中断丢事件 | 中 | 中 | 事件日志 + `Last-Event-ID` 续传 + 轮询兜底（已在设计内） |
| R-05 | 同步 Playwright 与异步框架冲突 | 中 | 高 | 后台 Worker 线程隔离（[修正-1]，已在设计内） |
| R-06 | 单步生成超时拖垮整轮 | 中 | 中 | 明确超时 + 分类重试 + 出错即中断；可配 `timeout_ms` |
| R-07 | 账号额度被外部刷爆 | 中 | 中 | 鉴权 + 限流 + 最大并发（[修正-7]，已在设计内） |
| R-08 | 单实例性能/内存瓶颈（浏览器进程多） | 中 | 中 | 上下文池 + 限并发；V2 再上多实例 + 分布式锁 |
| R-09 | 落盘内容含敏感信息长期滞留 | 低 | 中 | 留存期清理（NFR-09） + 日志不打正文 |

---

## 16. 演进路线（V2 候选，V1 一律不做）

明确推迟，避免在 V1 提前支付复杂度（继承架构宪法第 2 条）：

| 能力 | 触发条件 | 备注 |
|------|----------|------|
| 多实例横向扩容 + 分布式锁（Redis/文件锁） | 单实例并发不够 | 替换 Profile 进程内锁 |
| 自动断点恢复 / 重放（Replay） | 长流水线失败重跑成本高 | V1 直接重跑 |
| DAG / 并行分支 / Merge | 出现非线性编排需求 | V1 仅线性 |
| 人工接管 UI（noVNC）解验证码 | 验证码频繁阻断 | V1 报错重试 + 重新 Seeding |
| 自动评分 / 早停（满意即停） | 需要质量门控 | V1 由人决定"满意/再来一轮" |
| 数据库（检索/统计 Session） | 文件检索不够用 | V1 文件即数据库 |
| RAG / 知识库 / 插件 | 需要外部知识注入 | V1 仅 Prompt 接力 |

---

## 17. 附录

### 17.1 待决问题（开工前需拍板）
1. V1 首批 3 个模型清单，以及各自走 API 还是网页？（建议至少 1 个走 API，见第 2 节）
首批模型：claude、qwen、chatgpt、gemini、deepseek、https://chat.z.ai/、https://www.kimi.com/
API：gemini、qwen
2. 是否纳入 Grok（X 登录链路反爬更复杂，可后置）？
暂时不纳入
3. 鉴权 V1 用共享密钥是否可接受，还是直接上签名令牌？
鉴权用 API Key + HMAC签名
4. Session 留存期与隐私要求（默认 30 天是否合适）？
14天
5. 是否需要导出格式定制（单步 / 整轮 / 合并为单文档）？
必须支持 3种：单步输出（每个模型分开）+合并结果（给用户看）+JSON（给系统用）
### 17.2 与原方案的差异速查
本文新增：第 2（API vs 网页决策）、3（需求规格）、5（风险与对策）、7.3–7.7（事件/留存）、8（API 契约）、9（安全）、10（可观测/运维）、11（测试）、13–17（实施计划/验收/风险/演进/附录）节；并以 `[修正-1..8]` 标注了对原代码骨架的 8 处工程修正。原方案的产品定位、四条宪法、文件即数据库、配置驱动思想、Playwright 避坑经验全部保留。

### 17.3 参考
- 原文档：《RHCLOUD V1 完整技术与落地实施方案》（本方案的设计基线）。
- 实现时各网站的具体选择器/接口需以**当时实测**为准（DOM 与后端接口会变动）。
