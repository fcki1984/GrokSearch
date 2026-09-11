# Grok 与 Tavily 搜索分配设计

## 文档状态

| 项目 | 值 |
| --- | --- |
| 状态 | `LOCALLY_IMPLEMENTED_AND_VERIFIED` |
| 实现状态 | 已在任务分支完成本地验证与一次获授权的目标 quick smoke；未做 load/stress；未 commit/push |
| 跨上下文审查 | `CONSENSUS` 已应用且用户已确认；用户确认的时限修订和原生审查 finding 修正已整合 |
| 原生设计审查 | `APPROVE`（设计 SHA256 前缀 `5f920e9f`） |
| 设计基线 | `949807268b344b816930e12d53ea1c37f2dd6ce9` |
| 目标版本 | `0.2.1` |
| MAIN_SESSION_TOKEN_BUDGET | `15M incremental tokens` |
| token 基线计数 | 当前 host API 不提供主会话 `total_token_usage.total_tokens`，基线不可取得；不得编造，只能在可取得该计数的环境中开始累计 |

## DEVELOPMENT_TARGET

| 维度 | 已锁定目标 |
| --- | --- |
| 目标用户与位置 | 单用户、自托管、低延迟网络部署的 GrokSearch MCP；目标组合为 Grok2API 与 Tavily-compatible API，不配置 Firecrawl |
| 用户知识与权限 | 操作者能够配置现有环境变量并理解 provider 配额；产品不新增管理员、租户或权限模型 |
| 可接受操作 | 现有本地配置、模型和输入校验完成后，并发启动一次 Grok Search 和当前分配规则选中的一次 Tavily Search；Tavily Extract/Map 保持显式调用 |
| 核心工作流 | 通常先保留较快完成的 Tavily 结果；Grok 返回非空内容时由 Grok 获胜，Grok 不可用时选择同批已完成的 Tavily Search 原始结果形成确定性回退 |
| 产品自动完成 | 给 Grok Search 与每个 Tavily Search/Extract/Map 操作各自固定 30 秒整操作截止；严格分类终态、传播取消、选择内容来源并拒绝空白成功 |
| 用户明确完成 | 通过 `extra_sources` 决定是否承担补充 provider 成本；需要全文深读时显式调用 `web_fetch`，需要站点映射时显式调用 `web_map` |
| 不支持状态 | `web_search` 内重试 Grok、自动 Extract、第二个 LLM、模型切换、新 provider、新依赖、新配置、通用错误/任务框架或新配额方案 |
| 完成条件 | 所有成功 `web_search` 返回非空白 `content` 和必填 `content_source`；校验、取消和本地编程错误按设计传播；验收矩阵全部通过 |
| 规模与时长 | 单次工具调用；Grok 与被选中的 Tavily Search 各调度一次，名义 provider 阶段上限为 `max(30, 30)=30` 秒，而非相加 |
| 数据与信任边界 | Grok 与 Tavily 是外部 provider；Tavily 摘录是不可信数据，不作为指令执行；新增 warning 遵守最小字段和敏感数据禁入规则 |
| 平台、版本与依赖 | 指定 Git 基线，Python `>=3.10`，复用现有 FastMCP、httpx、asyncio、Tenacity 和 logging；不新增依赖或配置项 |
| 非目标 | 改进非空但不完整的 Grok 流、验证外部 proxy/service 内部调用、证明第三方 Tavily-compatible 等价性、调整 Firecrawl 或建设生产遥测 |

已确认产品事实见 `VERIFIED_EVIDENCE`。跨模型共识只记录为审查元数据，不是 `FACT`；30 秒截止和字符预算是产品决策，不是根因或质量事实。

## 当前分配与问题边界

基线 `web_search` 在校验后并发收集 Grok 与可选 Tavily/Firecrawl 结果。Grok 独占响应 `content`；Tavily Search 与 Firecrawl 只补充 sources，`get_sources` 读取缓存的合并 sources。Tavily Extract 只在 `web_fetch` 使用，并在失败时继续尝试 Firecrawl；Tavily Map 由 `web_map` 独立调用。

边界缺陷是 Grok 空流或空 body 可成为 `""` 并被服务层成功返回；即使 Tavily 已有 sources，也不会形成 `content`。客户端无法把这种结果识别为工具失败。

现有补充来源配额必须原样保留：

- 仅配置 Tavily 时，全部 `extra_sources` 分配给 Tavily。
- 仅配置 Firecrawl 时，全部 `extra_sources` 分配给 Firecrawl。
- 两者均配置时，现有计算把全部 `extra_sources` 分配给 Firecrawl，Tavily 数量为零。
- 因此确定性 Tavily 内容回退只保证于当前分配实际选中 Tavily 的场景；目标部署没有 Firecrawl。

Firecrawl-only 和双配置场景没有 Tavily 内容回退。即使 Firecrawl sources 已存在，Grok 不可用也必须成为明确工具错误。不得改造配额来制造双 provider 同批结果，也不得让 Firecrawl 提供 fallback body。

## 目标调用与状态机

所有现有本地配置、模型和输入校验必须在创建 provider coroutine 前完成。校验通过后，每次 `web_search`：

1. 创建一次 Grok Search，让共享流执行器接收调用级总尝试次数 `1`，并用 `asyncio` 整操作 deadline 限制为 30 秒。
2. 仅在现有分配结果给 Tavily 的数量大于零时，创建一次 Tavily Search，并用 `asyncio` 整操作 deadline 限制为 30 秒。
3. 按现有规则创建可选 Firecrawl 工作；它不参与回退 body，也不受本次时限保证约束。
4. 用 `asyncio.gather(..., return_exceptions=True)` 等待同批工作。
5. 在任何 source 合并、缓存、结果格式化或 Grok 分类前，扫描所有 gathered 槽位；任一槽位是 `asyncio.CancelledError` 都立即重新抛出。
6. 全槽取消扫描通过后，才按严格规则分类 Grok 槽位并选择 Grok 或已完成的 Tavily 内容。

```text
validate locally before creating provider coroutines
  |-- Grok Search once, whole-operation deadline 30s
  |-- selected Tavily Search once, whole-operation deadline 30s
  `-- Firecrawl unchanged and excluded from fallback body
               |
               `-- gather(..., return_exceptions=True)
                         |
                         `-- scan every slot for CancelledError first

Grok nonblank ----------------------> Grok content + existing merged sources
Grok unavailable + usable Tavily ---> deterministic raw-Tavily fallback
Grok unavailable + no Tavily ------> explicit tool error
any child/outer cancellation ------> propagate
local/programming error -----------> propagate
```

这不是先等待 Grok、再调用 Tavily 的串行流程。两者在校验后同时开始；Tavily 通常先完成并被保留，Grok 非空时获胜，Grok 不可用时选择已经完成的 Tavily。

| Grok 槽位终态 | Tavily 终态 | 行为 |
| --- | --- | --- |
| 非空白 `str` | 任意非取消终态 | 返回 Grok 内容和现有合并 sources，`content_source="grok"` |
| 空白/whitespace、自身整操作 `TimeoutError`，或合法请求边界产生的 `httpx.HTTPError` | 有可用原始结果 | 使用同批 Tavily Search 原始结果回退，`content_source="tavily_search_fallback"` |
| 同上不可用终态 | 未调用、空、失败或无可用项 | 抛出明确工具错误，绝不返回空白成功 |
| 任一 gathered 槽位为 `asyncio.CancelledError` | 任意 | 在任何结果处理前重新抛出 |
| Grok 为其他 `BaseException`、其他 `Exception` 或本地编程错误 | 任意 | 重新抛出，不用 Tavily 掩盖 |

外层取消继续沿 `gather` 传播。实现不增加手工 task 生命周期管理；聚焦测试必须覆盖 Grok 子任务、Tavily/Firecrawl 补充槽位及外层取消，且不使用长时间 sleep。

## 单次调用与错误分类

给现有 Grok 共享流执行器增加调用级“总尝试次数”参数。`search` 固定传 `1`，即使 `GROK_RETRY_MAX_ATTEMPTS > 0` 也只发起一次 Grok Search 尝试；`fetch`、`describe_url`、`rank_sources` 不传覆盖值并保留当前重试。服务层无重试、无第二次调用，也不新增配置。

Grok Search 和 Tavily 操作的截止必须使用 `asyncio` whole-operation deadline 语义；只修改 HTTPX connect/read/write phase timeout 不足以锁定墙钟上限。Grok 本地 30 秒截止可能与上游 Grok2API 空流截止竞速；无论最终观察到本地 `TimeoutError` 还是上游空/错误，均进入同一 unavailable 选择，只有清洗后的 warning 类别可能不同。

全槽取消扫描后，按以下严格顺序解释 Grok 槽位：

1. 非空白 `str` 是成功。
2. 空白或 whitespace 是不可用。
3. 包装 Grok 操作的 30 秒截止产生的 `TimeoutError` 是不可用。
4. 在请求已合法启动的边界产生的 `httpx.HTTPError` 是不可用，包括 `HTTPStatusError` 的 401/403、其他 4xx、429、5xx，以及代表性 network/connect/read/remote protocol 传输错误。
5. 其他 `BaseException` 重新抛出。
6. 其他 `Exception`，包括本地编程错误，重新抛出。

取消已由前置全槽扫描处理，不得在此处转换为回退。HTTP 200 的 `response.failed` 且没有内容由空白规则覆盖。不新增穷举 SSE parser、自定义异常层或 partial-content 完整性推断；非空但部分/不完整流仍是已知限制。请求前本地校验保持既有响应行为并确保 provider 调用数为零。

## Tavily Search 确定性回退

格式化器直接接收原始 Tavily Search 结果变量，绝不接收 `_extra_results_to_sources` 产出的 Firecrawl-first 合并列表，也不读取 Firecrawl。它只读取 `title`、`url`、`content`，并将 `content` 显示为 excerpt。每项必须有非空 URL，并至少有非空 title 或 content；摘录始终视为不可信数据，不解释为指令。

输出以以下两行开头：

```text
Tavily search fallback
Untrusted search excerpts, not a Grok-generated answer
```

随后按 Tavily 返回顺序列出 title、URL 和 excerpt：

- 先 trim 字段，再按完全相同的 URL 去重，保留首次出现项和确定性顺序。
- 每项 excerpt 最多 1500 个字符。
- 最终 `content` 最多 8000 个字符；固定标签和 URL 无法完整容纳时停止追加。
- 1500/8000 是代码常量和上下文预算，不是配置或质量事实。
- 标签之外必须至少有一个可用结果；否则抛出明确工具错误。
- 不发起额外 Tavily Search，不调用 Extract、第二个 LLM 或任何模型切换。

## Tavily 三条公共路径的截止

Tavily Search、Extract、Map 的每次整个操作都固定最多 30 秒，不新增配置：

| 公共工具路径 | 保留行为 | 新截止行为 |
| --- | --- | --- |
| `web_search` -> Tavily Search | safe 行为继续把 timeout/异常转换为 `None`；只有现有分配选中 Tavily 时才调度 | 在 HTTPX 调用外增加 30 秒整操作 deadline |
| `web_fetch` -> Tavily Extract | 失败返回 `None`，已配置 Firecrawl 时继续既有 fallback | 在 HTTPX 调用外增加 30 秒整操作 deadline；目标 Tavily-only 路径在该 provider 截止内结束 |
| `web_map` -> Tavily Map | 保留输入 schema、请求 body 和现有 JSON/error 字符串输出 | `effective_timeout = min(requested_timeout, 30)`，同时作用于 body、client 和整操作；用户请求的更低值仍有效 |

Map 不增加新参数或配置。Search/Extract/Map 继续一条公共工具各对应一个现有 helper。Firecrawl 在 `web_fetch` 中的顺序 fallback 可能让完整调用超过用户观察的外层 60 秒，它明确不属于本次目标保证。

## 公共响应契约与缓存

每个成功 `web_search` 响应新增必填字段：

```json
{
  "session_id": "...",
  "content": "nonblank",
  "sources_count": 1,
  "content_source": "grok"
}
```

`content_source` 只允许 `grok` 或 `tavily_search_fallback`。既有 `session_id`、`content`、`sources_count` 字段保留；source 合并、缓存和 `get_sources` 行为不变。必须保持 complete existing baseline tool set 的名称与输入 schema；`web_search` 描述只增加一句简短说明：Grok 不可用时可用同批 Tavily Search 摘录回退。

provider 调用前的本地校验错误对象（缺少 Grok 配置或显式模型无效）保留现有 error-object 响应形状；它们不是成功的 provider 响应，因此不受上述 `content_source` 不变量约束。

## 日志与隐私

只对本次新增 warning 规定边界。每次调用最多在使用 Tavily 回退或最终无回退可用时记录一条 warning，字段仅含：

- `session_id`；
- 已清洗的 outcome category，可含 auth 或 HTTP status class，但不含响应 body；
- Tavily result count；
- 该路径已经可取得的 elapsed time。

新增 warning 禁止记录原始 exception repr/body、query、content、URL、请求体、header、cookie、token 或 key；不为它新增 telemetry、全量跟踪或调试 UI。现有 debug 级 payload 日志保持不变并超出本次范围；本设计不声称整个 MCP 没有此类日志。

## 最小实现范围

实现只修改以下既有文件，不新增模块、依赖或配置键。该 implementation file allowlist 不含本设计文档；本设计文档是设计与交付证据工件，不是运行时实现文件：

| 文件 | 最小职责 |
| --- | --- |
| `src/grok_search/providers/grok.py` | 共享流执行器接受调用级总尝试次数；`search` 固定传 `1`，其他直接消费者保留现有重试 |
| `src/grok_search/server.py` | Grok/Tavily 整操作截止、Map clamp、并发 gather、全槽取消扫描、严格分类、Tavily-only 格式化、非空不变量、`content_source` 和 warning |
| `tests/test_regressions.py` | 聚焦锁定时限、调用数、取消/错误传播、配额、回退格式、缓存与 FastMCP metadata |
| `README.md` | 记录用户可见回退、`content_source`、一次 Grok Search 尝试及 Tavily 操作时限 |
| `docs/README_EN.md` | 同步英文用户文档 |
| `CHANGELOG.md` | 记录 `0.2.1` 已验证变化、公共契约、目标验证和限制 |
| `pyproject.toml` | 仅更新版本到 `0.2.1` |

不得加入 server retry、第二个调用层、provider/error/task 抽象或额外兼容分支。

## 验收矩阵

| 场景 | 必须证明的结果 |
| --- | --- |
| 并发调度 | 本地校验完成后 Grok 与被选中的 Tavily 同时开始；provider 调用数符合现有 allocation |
| Grok 返回非空白 | 返回 Grok 内容、现有合并 sources、`content_source="grok"` |
| Grok 返回空字符串或 whitespace | 有合格 Tavily 时确定性回退；否则明确报错，绝不空白成功 |
| Grok 整操作截止 | 用可控 coroutine/虚拟短 timeout 证明回退，不真实等待 30 秒 |
| Grok `HTTPStatusError` | 代表性状态含 401/403，并覆盖一个其他 4xx、429 或 5xx 类别；有 Tavily 时回退 |
| Grok transport error | 覆盖一个代表性 transport 类别即可，不穷举每个 subtype/status |
| 调用前校验失败 | 在创建任何 provider coroutine 前保留既有响应行为，provider 调用均为 0 |
| 本地编程错误 | 原样传播，不回退 |
| child `CancelledError` | 分别从 Grok 槽和 Tavily/Firecrawl 补充槽注入；所有 gathered 槽的 `CancelledError` 都在结果处理前传播 |
| outer `CancelledError` | 外层取消沿 gather 传播，不转为回退；取消测试不用长 sleep |
| 显式重试配置大于 0 | `web_search` 仍只有一次 Grok Search 尝试；至少一个其他 Grok 直接消费者保留重试 |
| Grok 不可用且 Tavily 缺席/未选中/空/错误 | 每种代表性路径均为明确工具错误 |
| 配额组合 | Tavily-only 全部分配给 Tavily；Firecrawl-only 全部分配给 Firecrawl；双配置全部分配给 Firecrawl且 Tavily 为零 |
| 格式化器 | 直接使用 raw Tavily 的 title/url/content；过滤、trim、URL 首次去重、顺序、1500/8000 上限、非空与标签确定 |
| source/cache | Firecrawl-first 合并、缓存及 `get_sources` 保持不变；Firecrawl 不进入 fallback body |
| Tavily Search | 整操作 30 秒 deadline；timeout/异常继续产生 `None` |
| Tavily Extract | 整操作 30 秒 deadline；`None` 与既有 Firecrawl fallback 行为保留 |
| Tavily Map | requested timeout 高于 30 时 clamp；低于 30 时保留；body/client/operation 使用同一 effective timeout，输出 shape 不变 |
| 公共响应 | 所有成功分支 content 非空，并含合法必填 `content_source` |
| 工具契约 | runtime initialize/list_tools 对 complete existing baseline tool set 做修改前后名称与输入 schema A/B；仅描述发生计划内变化 |
| 实例边界 | 2026-09-12 获授权的目标 quick smoke 已覆盖正常与受控回退场景；未做 load/stress，不证明生产可靠性 |

计划验证命令在实现分支执行；本设计批次不运行：

```bash
python -m pytest -q tests/test_regressions.py
python -m pytest -q tests/test_regressions.py -k "fastmcp_initialize_list_tools_metadata"
python -m pytest -q
python -m compileall -q src
```

聚焦测试使用可控 coroutine 和虚拟短 deadline，不用真实长等待。因共享 Grok 流执行器、三个 Tavily 公共路径和 server 响应契约改变，聚焦回归通过后运行当前完整 suite、compileall 与真实 FastMCP initialize/list_tools metadata A/B；主要证明完成后停止，不扩展压力、多平台或额外矩阵。

## 名义时延预算与实例边界

Grok Search 与 Tavily Search 并发，各自固定 30 秒整操作 deadline，因此 provider 阶段名义上限为 `max(30, 30) = 30` 秒。`web_search` 之前仍有可选、未缓存的显式模型 `/models` 校验，当前 HTTPX 上限为 10 秒；可选校验最多 10 秒加并发 provider 阶段最多 30 秒，目标路径名义合计约 40 秒，相对 `USER_OBSERVATION` 的外层 60 秒约有 20 秒余量。该数值是产品预算和名义边界，不是端到端 SLA。模型缓存锁没有显式 deadline，是保留风险，本次不增加修复。

上述不是整个 MCP 的硬性 60 秒 SLA：Firecrawl、锁等待、调度以及其他既有前后处理可增加总时长。目标保证只覆盖没有 Firecrawl、实际选中 Tavily 的目标部署与本次规定的 provider 操作。

2026-09-12 已获授权并完成一次目标 quick smoke，覆盖正常 Grok 内容路径与真实 Tavily 的受控回退路径。该结果不扩展为 load/stress、生产可靠性或端到端 SLA 证明；后续实例测试仍须单独授权并复用下述 runbook。

目标发布为 `0.2.1`。CHANGELOG 只写实现后已验证的用户可见行为、`content_source` 契约、目标验证和已知限制，不把未确认上游原因写成事实。本设计不授权 commit 或 push；未来 push 前应做隐私扫描。

## 已拒绝方案

| 方案 | 拒绝原因 |
| --- | --- |
| 只调 HTTPX phase timeout | 不能锁定解析、重试等完整操作的墙钟时间 |
| `web_search` 重试 Grok | 增加延迟和上游负载；同批 Tavily 已能提供可解释回退 |
| 回退时自动 Tavily Extract | 增加延迟、配额、上下文和 URL 选择问题，对阻止空白成功非必要 |
| 第二个 LLM 综合 | 增加依赖、成本和失败面并改变信任边界 |
| 自动切换模型 | 隐式改变操作者选择，且不能修复边界分类 |
| 改造双 provider 配额 | 偏离现有公共行为，且目标部署没有 Firecrawl |
| catch-all 回退或新 task/error 框架 | 会掩盖取消和本地缺陷，并为局部状态机引入不必要抽象 |

## 风险与明确限制

- Tavily 摘录缺少 Grok 回答的综合能力，回退质量较低但可解释。
- 已授权目标中的 Tavily-compatible service 仅通过一次 quick smoke；其他兼容实现及更广场景未经证明。
- 持续 HTTP 认证失败会按 availability-first 契约成为可见 Tavily 回退，而非原工具失败；warning 只暴露清洗后的 auth/status 类别。
- 固定 30 秒是用户确认的延迟/可用性取舍，不是唯一正确阈值。
- 可选模型校验和无显式 deadline 的缓存锁发生在 provider 之前，会消耗外层预算。
- Firecrawl-only 与双配置不在 60 秒或 Tavily 内容回退保证内；`web_fetch` 的顺序 Firecrawl fallback 也可能超过外层预算。
- 非空但不完整的 Grok 流不会触发回退。
- `extra_sources=0`、allocation 未选中 Tavily或 Tavily 无可用项时不存在 fallback。
- Firecrawl 调用与配额行为保持不变，且永不提供 fallback body。

## INSTANCE_TEST_RUNBOOK

- 适用基线：版本 `0.2.1`、运行时补丁 SHA256 `8673047f0d4ac83c13e94c920b3bb39ff8f1928e8d3c4aae3e6ed766cfa19340`、Tavily-only/no Firecrawl；凭据与服务地址仅由现有 Grok/Tavily 环境变量在进程中提供，文档和产物不得记录值；默认模型 `grok-chat-fast` 必须可用。
- 初始化与认证：按现有部署配置引用 Grok 和 Tavily endpoint/key 环境变量，不复制凭据。readiness 依次要求 Grok `/models` 返回成功且含默认模型、最小 no-tool chat 返回非空内容、Tavily `/search` 返回至少一项可用结果；任一步失败即归为测试环境阻塞并停止。
- 稳定接口：使用真实 FastMCP Client 调用 `web_search`，设置 `extra_sources=1`，不显式传 model/platform。正常场景必须非 error，session/content 非空，`content_source="grok"` 且 `sources_count=1`。
- 受控回退：仅在测试进程内把 `GROK_API_URL` 指向 loopback refused endpoint，保留真实 Tavily 与 no Firecrawl 配置；调用同一接口后必须非 error、content 非空、`content_source="tavily_search_fallback"`、`sources_count=1`，并同时含两条规定的 fallback 标签。
- 证据与清理：只记录时间、版本/补丁、状态、elapsed、source/count、标签布尔值、长度/哈希及 artifact SHA；不记录 query、content、endpoint 或 key。证据文件权限保持 `0600`；结束后恢复进程环境，并确认仓库状态不变且没有仓库内产物。
- 失效条件：源码补丁哈希或版本变化，provider/API/auth/default-model、allocation 或成功响应契约变化时，必须重新核验本 runbook。

## VERIFIED_EVIDENCE

- `FACT`：不可变源码基线 `949807268b344b816930e12d53ea1c37f2dd6ce9`；`server.py` SHA256 `dba33c2f96be702e62507f0b0558828d176afb21f631aa238626f913895949a3`，`grok.py` SHA256 `52cb31f937cb100926a6160cc10a447951cf6f628b5238c8ec5696312aad1c3c`。服务端校验后并发收集 Grok 与可选 Tavily/Firecrawl；当前 gather 不返回异常值，空 Grok 被归一为 `""`，Tavily/Firecrawl 只进入 sources。双配置的现有 allocation 把全部补充数量给 Firecrawl；证明范围仅限这些指纹，文件变化即失效。
- `FACT`：同一基线的完整现有工具集合恰为 `get_config_info`、`get_sources`、`switch_model`、`toggle_builtin_tools`、`web_fetch`、`web_map`、`web_search`；公共注册位置已冻结于 `server.py`。该基线事实只定义比较对象，实施后的契约证据见下述 runtime metadata A/B。
- `FACT`：同一基线 Grok 共享执行器的 HTTPX connect/read/write phase timeout 为 6/120/10 秒并使用 Tenacity 配置值加一次尝试；直接消费者为 search、fetch、describe_url、rank_sources。Tavily Extract/Search/Map 的现有 HTTPX 上限分别为 60/90/请求值加 10 秒；Map 默认请求值为 150。它们只证明当前实现，不能替代目标整操作 deadline。
- `FACT`：匿名化导出会话 SHA256 `c3296208952d93abdc24eaf520907937aa8ff902efd7e5c9ca571205f5df8666` 含 11 次 `web_search`：7 次非空、2 次 MCP timeout、2 次 `content=""` 且 `sources_count=5`。该证据只证明 MCP 空成功与同时存在 sources，不证明上游机制或通用时延。
- `FACT`：Python 3.12.4 证据环境、项目要求 Python `>=3.10`；该运行时 `asyncio.CancelledError` 派生自 `BaseException`，外层取消沿 `asyncio.gather` 传播。该语言级事实不替代本项目回归证据；测试门由下述实施 FACT 单独记录。
- `FACT`：2026-09-11 核验的官方 Tavily 文档说明 Search 返回结果片段，Search 与 Extract 分离，Extract 支持 URL 批次和 raw content。只证明官方参考契约，不证明第三方 compatible endpoint。
- `FACT`：目标版本 `0.2.1` 的任务分支 `codex/implementation-grok-tavily-allocation` 已形成运行时源码补丁，补丁 SHA256 `8673047f0d4ac83c13e94c920b3bb39ff8f1928e8d3c4aae3e6ed766cfa19340`；当前 `server.py` / `grok.py` SHA256 分别为 `64da9bd573702cd14a6bad48b078b9ea92d91a70955c432406bd690e28c33054` / `635b1ceae2f6f57d10df06e96726be474bcc6fcbcd6090a08f9a30b7c06dddd9`。证明范围限于这些指纹下的实现；未 commit/push。
- `FACT`：Python 3.12.4 环境中，聚焦回归与完整发现 suite 均为 30/30、exit 0、无 warning，且未联网或调用 live provider；源码与测试 `compileall` 通过，最后一次仅测试改动后测试 `compileall` 再次通过。测试文件最终 SHA256 `aa55d0dfe095ffefc01487d5fd4a84ec768be598400416f67956ee3829c2f92d`；只证明隔离回归、完整已发现测试与编译门。
- `FACT`：同一实现上真实 FastMCP initialize/list_tools 通过，工具名恰为既有七项；仅规范化 `web_search.description` 后的前后比较契约 SHA256 同为 `9baa229f6fe112eaaca2d808674f5e906fcf524c84af5400f1dbd859edb56c00`，`web_search` inputSchema SHA256 前后同为 `ccfd7e0f883dde805f6de9e21849577ddb0dee1a5d4a1e0f2c714efb2e2f3966`。只证明本地 FastMCP 元数据与 schema 契约。
- `FACT`：交付静态门中 `git diff --check` 通过，`pyproject.toml` 解析版本为 `0.2.1` 且依赖未变，changed-file 集合与批准范围一致，凭据/私有主机扫描为零匹配。以上静态检查本身不构成 live provider、remote MCP instance 或端到端 SLA 证明。
- `FACT`：2026-09-12 在基线 `949807268b344b816930e12d53ea1c37f2dd6ce9`、版本 `0.2.1` 与运行时补丁 SHA256 `8673047f0d4ac83c13e94c920b3bb39ff8f1928e8d3c4aae3e6ed766cfa19340` 上完成获授权的目标 quick smoke；Grok `/models` 为 HTTP 200、返回 20 个模型且含默认模型，最小 no-tool chat 为 HTTP 200/非空，Tavily `/search` 为 HTTP 200/一项可用结果。真实 FastMCP Client 正常路径耗时 8.668 秒，非 error 且 session/content 非空，`content_source="grok"`、`sources_count=1`、content 长度 2297/SHA256 前缀 `b3b8a7d822a2d255`；受控 Grok loopback-refused 路径保留真实 Tavily，耗时 4.493 秒，非 error，`content_source="tavily_search_fallback"`、`sources_count=1`、content 长度 1732/SHA256 前缀 `2e71c5fd756490b5`，且两条规定标签均存在；两条核心路径合计 13.163 秒且没有 retry/failure。来源 `/tmp/groksearch-live-smoke-20260911/result-corrected.json` 权限为 `0600`，corrected artifact SHA256 为 `fb9605665e9aa8e20283402ba5c27ee66120670745d969cb46343ec27cf0c7bb`；仓库状态不变且无仓库内产物。该事实只证明上述两种场景，不证明 load/concurrency、生产可靠性/SLA、所有上游失败或 Grok2API 根因。
- `USER_OBSERVATION`：当前远程 MCP 请求 timeout 为 60 秒；Grok Web 成功请求总耗时小于 20 秒；Grok2API 空流截止为 30 秒；Tavily Search 不超过 5 秒，Extract/Map 不超过 12 秒；目标为低延迟 Netcup 部署，Tavily 稳定而 Grok2API 存在 HTTP 200 空失败。这些未经同基线受控实验，不是根因或 SLA 事实。

官方 Tavily 参考：

- <https://docs.tavily.com/documentation/api-reference/endpoint/search>
- <https://docs.tavily.com/documentation/api-reference/endpoint/extract>
- <https://docs.tavily.com/documentation/best-practices/best-practices-search>
- <https://docs.tavily.com/documentation/best-practices/best-practices-extract>
- <https://docs.tavily.com/documentation/rate-limits>

## DIAGNOSTIC_STATE

`ROOT_CAUSE=UNKNOWN`

| Claim | 状态 | 依据与边界 |
| --- | --- | --- |
| MCP 边界允许空 Grok 结果成为成功响应，同时已有 Tavily sources 未用于 content | `CONFIRMED` | 基线源码与匿名化导出共同命中空成功及同时存在 sources；只确认仓库/MCP 边界缺陷 |
| 当前实现阻止已测试 blank、deadline、HTTPX 和无可用 fallback 路径返回空白成功 | `CONFIRMED` | 同一实现指纹下的受控聚焦回归命中预期；目标 quick smoke 又证明正常 Grok 内容与受控 Tavily 回退两条真实 FastMCP 路径返回非空且来源分类正确。只证明已覆盖测试与这两个实例场景 |
| Grok2API 不完整或提前结束 SSE 导致本次空 content | `HYPOTHESIS` | 与用户观测和 provider 空流路径相容，但没有同次关联的 outbound/stream 或受控 A/B |

DESIGN_REVIEW_OPTIONS：设计文档已完成。实现前选择：1）一次原生独立设计审查；2）Web Sol 与 Codex Sol 人工中转互评；3）先互评收敛、经用户确认更新最终文档后再执行一次原生独立设计审查；4）不审查。
