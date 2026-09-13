# 🧘 健康计划生成助手（Health Planner）

基于 **LangGraph 多智能体协作 + 分层记忆 + 人机协同**的对话式健康计划助手：输入身体信息与健康目标，多个专职 Agent 协作生成「饮食 + 运动 + 作息」结构化健康计划；支持**对话式追问调整**，内置**三层安全护栏**、**两个人工确认闸门**与**全链路可观测性**。

> 最初参照 Datawhale「Hello-Agents」教程第十三章「智能旅行助手」搭建了多智能体骨架；
> 此后的**分层记忆、三层安全护栏、人机协同闸门、可观测性与测试体系**均为独立改造，
> 取舍理由见下方「架构选型」。

---

## ✨ 特性

- 🤖 **多智能体编排**：7 个 LLM 节点（其中 4 个是带 MCP 工具的 ReAct 子代理）+ 1 个规则意图路由
- ✋ **人机协同（Human-in-the-loop）**：高风险闸门 + 计划确认闸门，用 LangGraph `interrupt` 实现，检查点落盘、服务重启后可恢复
- 🔁 **带上限的调整回边**：确认闸门与计划调整构成一条真实环，`MAX_REVISIONS` 防死循环
- 🧠 **分层记忆**：短期（会话多轮）+ 长期（画像 / 体重历史 / 上一版计划），跨会话持久化
- 🛡️ **三层安全护栏**：确定性规则预判 + LLM 判断（取更严者）+ 免责声明强制注入；意图无法识别时 **fail-closed**
- 📈 **可观测性**：`trace_id` 贯穿请求 → 节点 → 模型 → 工具，结构化 JSONL 落盘；`/api/metrics` 直接回答"延迟多少、花了多少钱、失败在哪一步"
- 🔧 **MCP 工具集成**：FastMCP 暴露 6 个本地健康工具
- ⚡ **SSE 流式**：逐节点推送进度，前端实时可视化
- ✅ **202 个单元测试**：含 8 个标了 `gap` 的"当前行为即缺口"快照用例
- 📊 **自研评测体系**：单模型 vs 多智能体对比 + 客观校验

---

## 🏗️ 架构选型：为什么是 workflow，而不是让 LLM 自主编排？

这是本项目最需要解释的一个决定。

**场景约束**：健康场景的评估维度是**固定**的——画像 → 风险 → 膳食 / 运动 / 作息 → 合成。少跑一个维度不是"更灵活"，而是**漏掉一项安全评估**，属于事故；而且计划要能对用户解释"为什么这么建议"。

**所以选了「稳定 workflow + 节点内 ReAct」而不是自主多智能体：**

| 决策 | 选择 | 理由 |
|---|---|---|
| 编排 | LangGraph **静态 DAG** | 可审计、可复现、可测试。哪一步跑了、跑了多久，都能从 trace 里查到 |
| 谁来选工具 | **节点内的 `create_agent`** | 工具选择交给模型（"该不该查食物营养"确实是它更懂），但**评估哪些维度**不交给它 |
| 风险等级 | **确定性规则层兜底** | `stricter_level(规则, LLM)` 取更严者，不让 LLM 单方面把高风险判成低风险 |
| 是否需要人确认 | **显式闸门**，不是模型自己决定 | 高风险必须停下来问人，这是产品与合规要求，不能是"模型的判断" |

换句话说：**把"灵活性"留在工具调用层，把"确定性"留在编排层。**

> 参考《什么样的 Agent 项目才算好项目》的说法："多 Agent 不是越多越好……用稳定 workflow 反而更可控。**好的架构不是炫技，而是匹配业务。**"

如果要换成自主编排（LLM 决定跑哪些评估维度），代价是：安全性从"结构性保证"退化成"概率性保证"。这个取舍我认为在健康场景不划算。

---

## 🤖 多智能体架构

```
START ─┬─ generate → 画像解析 → 健康评估 → 🚦风险闸门 ─┬─(中止)→ 🛑就医建议 → END
       │                                              └─(继续)→ 分发 ─┬─ 膳食规划 ─┐
       │                                                              ├─ 运动规划 ─┼→ 计划合成 → 🚦计划确认 ─┬(接受)→ END
       │                                                              └─ 作息规划 ─┘                    └(调整,≤3轮)→ 计划调整 ─┘
       └─ adjust → 计划调整 → 🚦计划确认
```

| 节点 | 类型 | 职责 | MCP 工具 |
|---|---|---|---|
| 🧭 意图路由 | 规则（非 LLM） | 首次生成 / 追问调整分流 | — |
| 👤 画像解析 | LLM | 自然语言 → 结构化画像（JSON） | — |
| 🩺 健康评估 | **ReAct 子代理** | BMI + 风险把关 + 就医建议 | calculate_bmi, query_health_knowledge |
| 🥗 膳食规划 | **ReAct 子代理** | 热量/宏量/三餐 | query_food_nutrition, query_health_knowledge |
| 🏃 运动规划 | **ReAct 子代理** | 一周运动计划 | get_exercise_guidance, get_weather, query_health_knowledge |
| 🌙 作息规划 | **ReAct 子代理** | 睡眠/节律 | get_sleep_guidance, query_health_knowledge |
| 📋 计划合成 | LLM（JSON 模式） | 汇总 → 结构化 JSON（最多重试 2 次，失败降级 `raw_plan`） | — |
| 🔧 计划调整 | LLM（JSON 模式） | 基于上一版计划 + 追问增量调整 | — |
| 🚦 风险闸门 | 闸门 | `need_medical` 时暂停问人（**fail-closed**） | — |
| 🚦 计划确认 | 闸门 | 产出后问"接受 / 调整"，带 3 轮上限 | — |
| 🛑 就医建议 | LLM-free | 用户中止时产出就医建议，**不生成计划** | — |

> **关于"几个 Agent"**：早期的 README 写 6 个，实际是 7 个 LLM 节点（见上表）。
> 差异来源于"意图路由"是否算作一个 Agent——它其实是纯规则函数，不调模型，所以没计入。

### 三路并行与串行

`health_graph.py` 里的 `PARALLEL` 开关控制膳食 / 运动 / 作息是并行 fan-out 还是串行链。
并行更快但容易撞 DeepSeek 限流；两种模式都有测试覆盖（`tests/test_graph_structure.py`）。

---

## ✋ 人机协同（Human-in-the-loop）

两个闸门都基于 LangGraph 的 `interrupt` + checkpointer：

```
用户提交 → 图开始跑 → 撞到闸门 → interrupt 抛出特殊信号
                                    ↓
                        状态存入 checkpointer，SSE 下发中断帧
                                    ↓
                        前端渲染确认卡片 → 用户点选 → POST /api/chat/resume
                                    ↓
                        Command(resume=...) 接着跑同一个 thread_id
```

**闸门一 · 风险确认**：健康评估判定 `need_medical=True` 时暂停。

- 选项：`continue`（我已了解，继续生成） / `abort`（先去看医生，暂不生成）
- **选中止就真的不生成计划**，只给就医建议——"系统提供了不做事的选项，而且它真的生效"
- **意图识别不出来时按中止处理（fail-closed）**：在需要就医的场景里，误判成"继续"的代价远大于误判成"中止"

**闸门二 · 计划确认**：计划产出后暂停，选项 `accept` / `revise`（可填调整意见）。

- 点 `revise` 会带着意见回到「计划调整」节点，形成一条**真实回边**，改完再次进入确认
- **`MAX_REVISIONS = 3` 是防死循环的硬闸**：没有上限时用户可以无限要求调整，token 成本也随之无上限

**检查点持久化**：用 `AsyncSqliteSaver` 落到 `backend/data/checkpoints.sqlite`，而不是内存版 `MemorySaver`——否则服务一重启，所有暂停中的会话就全丢了，"人机协同"就退化成"人必须在进程活着的时候点确认"。

命令行版（`cli.py`）同样接入了闸门，不需要前端就能验证整条链路。

---

## 📈 可观测性

对应《什么样的 Agent 项目才算好项目》第五节的要求，`observability.py` 让每次请求都能回答这些问题：

| 问题 | 怎么回答 |
|---|---|
| 每次请求调用了哪些 Agent？ | `GET /api/trace/{trace_id}`，看 `kind="node"` 事件 |
| 每次调用用了哪个模型？ | `kind="llm"` 事件的 `model` 字段（实测抓到 `deepseek-flash`） |
| 工具调用是否成功？ | `kind="tool"` 事件的 `ok` 字段 + `totals.tool_success_rate` |
| 失败发生在哪一步？ | `failures_by_step` + `ok=false` 事件的 `name` / `error` |
| 平均响应时间是多少？ | `latency_ms`，按 **节点 / 模型 / 工具** 分别给 P50 与 P95 |
| Token 成本是多少？ | `cost.total_tokens` + `cost.cost_cny`（单价可配，见下） |
| 用户是否接受最终结果？ | `POST /api/feedback` → `feedback.accepted_rate` |
| 输出结果准确率如何？ | 离线由 `eval.py` 给出，见「评测结果」 |

**实现要点**

- **采集方式**：用 LangChain 的 `BaseCallbackHandler`，在 **config 层**注入（`hg.run_config()`），而不是在业务代码里逐点埋点。
  这里踩过一个坑：回调挂在**模型构造函数**上只能覆盖 LLM 调用；`create_agent` 内部 ToolNode 发起的工具调用是另一个 run，拿不到模型上的局部回调——实测"工具调用数 = 0"，改到 config 层后立刻正常（26+ 次）。**注意别两处都挂**，否则 token 会记两遍。
- **trace 传递**：`contextvars`，asyncio 子任务与 `to_thread` 都会继承（有测试覆盖）。
- **落盘格式**：JSON Lines（`logs/trace.jsonl`），一行一个事件，`grep` / `jq` / pandas 都能直接消费，不需要额外架一个可观测平台。
- **fail-open**：日志写不进去时不能影响主流程（有测试覆盖）。
- **成本单价**：`PRICE_INPUT_PER_M` / `PRICE_OUTPUT_PER_M` 环境变量可覆盖，默认值标注为估算——**价格会变，成本数字必须能追溯到来源**。

---

## 🛡️ 安全护栏（三层 + 闸门）

1. **规则化预判**（确定性）：`safety.py` 纯 Python 算 BMI + 疾病关键词，产出硬结论。
2. **LLM 判断**：健康评估子代理结合工具判断，后端 `stricter_level(规则, LLM)` **取更严一档**。
3. **免责声明强制注入**：无条件覆盖 `meta.disclaimer`；**非 dict 输入也不崩**（见「已知限制」里的修复记录）。
4. **人工闸门**：`need_medical=True` 必须经过用户确认才能继续。

高风险判定门槛（`rule_based_risk`）：

| BMI | 等级 | 是否建议就医 |
|---|---|---|
| < 18.5 | high | ✅ |
| 18.5 – 23.9 | low | ❌ |
| 24.0 – 27.9 | medium | ❌ |
| ≥ 28.0 | high | ✅ |
| 任意 BMI + 有基础疾病 | high | ✅ |

---

## 🧠 分层记忆

| 记忆 | 范围 | Key | 内容 | 过期 |
|---|---|---|---|---|
| 短期 | 会话级 | `session:{sid}:messages` | 多轮对话 | 1 小时 |
| 长期 | 用户级 | `user:{uid}:profile` | 用户画像 | ⚠️ 目前不过期 |
| 长期 | 用户级 | `user:{uid}:weight_history` | 体重历史趋势 | ⚠️ 目前不过期 |
| 长期 | 用户级 | `user:{uid}:last_plan` | 上一版计划 | ⚠️ 目前不过期 |

存储引擎由 `REDIS_URL` 环境变量决定（`memory.py` 用 redis-py 标准 API，两种模式代码完全一致）：

| 模式 | 触发条件 | 数据活多久 |
|---|---|---|
| **真 Redis** | 设了 `REDIS_URL` | 跨服务重启、跨容器重启都在（配合 AOF） |
| `fakeredis` | 没设 `REDIS_URL` | **进程内存，服务一重启就没了** |

推荐用 Docker 起一个（Redis 只占几十 MB，不像 Milvus 那样吃内存）：

```bash
# 国内直连 Docker Hub 通常会超时，先用镜像源拉
docker pull docker.m.daocloud.io/library/redis:7-alpine
docker tag  docker.m.daocloud.io/library/redis:7-alpine redis:7-alpine

# --appendonly yes 开 AOF；命名卷让数据在容器重建后仍在
docker run -d --name health-planner-redis --restart unless-stopped \
  -p 6379:6379 -v hp-redis-data:/data \
  redis:7-alpine redis-server --appendonly yes
```

然后在 `backend/.env` 里写 `REDIS_URL=redis://localhost:6379/0`。

**为什么不是 fakeredis**：它的数据存在 Python 进程内存里，服务一重启，用户画像、体重历史、会话多轮全丢。
这与"分层记忆 / 跨会话个性化"这个卖点是直接冲突的——演示时重启一次就露馅。
`fakeredis` 保留为默认值只是为了"clone 下来不装任何东西也能跑通"。

> 注意：`session:*` 有 1 小时 TTL（短期记忆本来就该过期），
> `user:*` 三个键**没有 TTL**（见「已知限制」）。

---

## ✅ 测试

```bash
make test          # 或：.venv/Scripts/python.exe -m pytest backend
make test-gaps     # 只列已知缺口用例（= 待修清单）
```

**202 个用例，全部通过：**

| 文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `test_safety.py` | 92 | BMI 五档分级与三处边界、疾病强制升级、脏输入容错、取更严者、报告解析、JSON 兜底、免责声明、**护栏组合语义** |
| `test_hitl.py` | 39 | 两个闸门的决策逻辑、fail-closed 方向、中断载荷字段、路由、中止分支 |
| `test_observability.py` | 38 | trace 上下文传播、事件落盘、span、节点包装、LLM/工具回调、成本换算、聚合、fail-open |
| `test_memory.py` | 26 | 短期/长期记忆往返、TTL、并发丢更新、引擎切换 |
| `test_graph_structure.py` | 7 | 编译、fan-out、条件边分叉、**回边存在性**、串行模式 |

**8 个 `gap` 用例**断言的是"当前行为即缺口"，不是期望行为——跑 `make test-gaps` 就能看到待修清单。

### 补测试时挖出的两个真 bug

补测试之前，这套系统的护栏部分**没有任何测试**。写测试的过程直接挖出两个会打穿整个流程的缺陷：

1. **`rule_based_risk` 崩溃**：`except` 只捕了 `(TypeError, ZeroDivisionError)`，而 `float("1.7米")` 抛的是 **ValueError**。
   画像由 LLM 解析、不做类型强转——用户说"我身高一米七"，模型输出 `"1.7米"`，**整个 graph 就崩在护栏内部**。
2. **`ensure_disclaimer` 崩溃**：用 `setdefault("meta", {})`，当键存在但值不是字典时（LLM 产出 `{"meta": "无"}`）不替换，下一行 `obj["meta"]["disclaimer"] = ...` 直接抛 `TypeError`。
   这个函数的承诺是"保证免责声明 100% 出现"，结果它自己崩掉 → **免责声明 100% 不出现**，与承诺正好相反。

两个都已修复并有回归用例。

---

## 📁 目录结构

```
health-planner/
├── Makefile                    # make test / test-gaps / install-dev / run
├── backend/
│   ├── mcp_health_server.py    # FastMCP 6 个 mock 工具
│   ├── health_graph.py         # LangGraph 编排（闸门 + 回边 + 防死循环）
│   ├── observability.py        # 结构化日志 + Trace + 聚合指标
│   ├── memory.py               # 分层记忆（fakeredis，可切真 Redis）
│   ├── state.py                # State + 初始状态 + MAX_REVISIONS
│   ├── prompts.py              # 各 Agent system prompt
│   ├── safety.py               # 安全护栏（纯函数，被测试覆盖最密）
│   ├── cli.py                  # 命令行对话版（含终端内的确认闸门）
│   ├── server.py               # FastAPI + SSE + resume + metrics
│   ├── eval.py                 # 评测（单模型 vs 多智能体）
│   ├── conftest.py             # 测试引导（sys.path + 环境变量）
│   ├── pytest.ini
│   ├── requirements.txt
│   ├── requirements-dev.txt    # pytest / pytest-asyncio
│   ├── tests/                  # 202 个用例
│   └── data/checkpoints.sqlite # 中断检查点（gitignore）
└── frontend/
    └── src/
        ├── views/Planner.vue           # 主页面（含中断 → 确认 → 恢复闭环）
        ├── components/ConfirmCard.vue  # 确认卡片
        ├── components/                 # ProgressSteps / PlanCard / RiskBanner
        └── api/sse.ts                  # postSSE 共用解析 + resumeChat
```

---

## 🚀 快速开始

```bash
# 0. Redis（可选但推荐：不装的话记忆一重启就没了，见「分层记忆」）
docker run -d --name health-planner-redis --restart unless-stopped \
  -p 6379:6379 -v hp-redis-data:/data \
  redis:7-alpine redis-server --appendonly yes

# 1. 后端环境
cd backend
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
cp .env.example .env          # 填 DEEPSEEK_API_KEY；接了 Redis 就一并填 REDIS_URL

# 2. 命令行版（最快验证人机协同：闸门会直接在终端里问你）
.venv/Scripts/python.exe cli.py

# 3. Web 版
.venv/Scripts/python.exe -m uvicorn server:app --reload

# 4. 前端
cd ../frontend && npm install && npm run dev    # http://localhost:5173

# 5. 测试
cd .. && make test
```

### 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | SSE 流式对话，可能在闸门处暂停 |
| POST | `/api/chat/resume` | 恢复暂停中的图，body `{thread_id, decision, feedback}` |
| POST | `/api/feedback` | 记录用户是否接受结果 |
| GET | `/api/memory/{user_id}` | 长期记忆 |
| GET | `/api/metrics` | 聚合指标 |
| GET | `/api/traces` | 最近请求列表 |
| GET | `/api/trace/{trace_id}` | 单次请求完整事件链 |
| GET | `/api/health` | 存活探针 |

---

## 📊 评测结果

> ⚠️ **这一节的样本量偏小，结论仅供参考。** 4 个病例 × 单次 LLM 裁判，见「已知限制」。

4 个病例（减脂-超重 / 增肌-正常 / 养生-高血压 / 减脂-低体重）× LLM-as-judge 四维度打分：

| 维度 | 单模型 | 多智能体 | 变化 |
|---|---|---|---|
| 完整性 | 8.8 | 9.8 | ↑ 1.0 |
| 个性化 | 7.8 | 9.5 | ↑ 1.8 |
| 安全性 | 8.5 | 9.5 | ↑ 1.0 |
| 一致性 | 8.5 | 8.0 | ↓ 0.5 |
| **总平均** | **8.4** | **9.2** | **↑ 0.8** |

客观校验（免责声明 / 就医建议 / BMI 一致性）**24 项全 PASS**。

评测脚本现在会统计**每次生成触发了几个闸门**（`hitl_interrupts`）——
如果 100% 的用例都触发风险闸门，说明闸门门槛设得太松、形同虚设；这个数字是衡量的前提。

---

## ⚠️ 已知限制

诚实列出，不当作没看见：

**安全与正确性**

1. **`parse_risk` 是 fail-open**：报告里没写"风险等级"或写了个没收录的值（如"极高"）时，静默降级成 `low/False`。
   目前靠 `stricter_level(规则, LLM)` 兜住才没出事，但护栏自身应当 fail-closed。**已用 `gap` 用例钉住当前行为。**
2. **`stricter_level` 对非法等级的处理不对称**：`_SEVERITY.get(未知, 0)` 把未知等级当 0，于是"未知"在左边会胜出、在右边会被丢掉。同样已标记。
3. **三层护栏的第二层依赖 LLM 输出的格式**：`parse_risk` 是正则抽取，模型换一种句式就可能抽不到（见第 1 条）。

**记忆与并发**

4. **三个长期键都没有 TTL**，会无限增长。
5. **`save_profile` / `save_last_plan` 用 `set` 整体覆盖**，没有 CAS 或版本号：并发的"读-改-写"会静默丢字段。`append_message` / `append_weight` 用 `rpush` 是原子的，没问题。

**工程**

6. **评测样本量小**：4 个病例、每个维度一次打分，且 `judge()` **一次调用同时评两份计划**——存在位置偏见；同一模型既生成又评判，存在自我偏好偏见。一个病例波动就能翻转结论。
7. **`health_graph.py` 在导入期校验 API Key**，缺失时 `sys.exit(1)`。这让模块默认无法被测试导入（`conftest.py` 里塞了占位值绕开）。更好的做法是延迟到首次建图时再校验。
8. **中断的会话可能长期滞留**在 `checkpoints.sqlite` 里：用户提交后不点确认就关掉页面，那条线程会一直留着。目前没有清理策略。
9. **`PARALLEL = False` 的串行分支**此前从未被执行验证过，现在有结构测试覆盖，但仍未做过真实的串行端到端跑测。
10. **可观测性数据存在进程内存**（事件、聚合指标），重启即清空；只有 JSONL 文件是持久的。上生产应换 Prometheus / Langfuse。

**上游依赖**

11. **DeepSeek 限流**：三路并行会同时多次调用 LLM，遇到限流（请求卡住）等几分钟自动恢复，或把 `health_graph.py` 的 `PARALLEL` 改为 `False` 退串行。
12. **MCP stdio**：`mcp_health_server.py` 内禁止 `print`（会污染 stdio 握手）。
13. **Windows venv**：本机 `python` 可能指向别的项目 venv，务必用 `.venv/Scripts/python.exe` 绝对路径。
14. **`user:*` 三个长期键没有 TTL**，会无限增长；`session:*` 有 1 小时 TTL 是对的。用 `redis-cli ttl` 可以直接看到（`-1` = 永不过期）。
15. **不设 `REDIS_URL` 时退回 `fakeredis`，记忆只活在进程内存里**，服务重启即清空。默认这样是为了"clone 下来零依赖能跑"，但**演示时应该接真 Redis**（见「分层记忆」一节）。

---

## 📄 免责声明

本项目的健康计划仅供科普与学习用途，不构成医疗建议。
