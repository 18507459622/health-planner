# 🧘 健康计划生成助手（Health Planner）

基于 **LangGraph 多智能体协作**的健康计划生成系统：输入身体指标（性别/年龄/身高/体重）与健康目标（减脂/增肌/养生），6 个专职 Agent 协作生成「饮食 + 运动 + 作息」结构化健康计划，并内置**三层安全护栏**（风险识别 + 就医建议 + 免责声明）。

> 参照 Datawhale「Hello-Agents」教程第十三章「智能旅行助手」的多智能体架构改造而来。

## ✨ 特性

- 🤖 **6-Agent 多智能体编排**：画像解析 → 健康评估 →（膳食 / 运动 / 作息三路**并行**）→ 计划合成
- 🛡️ **三层安全护栏**：确定性规则预判 + LLM 判断 + 免责声明强制注入，高风险输入必提示就医
- 🔧 **MCP 工具集成**：FastMCP 暴露 6 个本地健康工具，`MultiServerMCPClient` 注入智能体
- ⚡ **SSE 流式**：后端逐节点推送进度，前端实时可视化
- 📊 **自研评测体系**：单模型 vs 多智能体对比 + 客观校验

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| 编排 | LangGraph（有向图 + 三路并行 fan-out） |
| 大模型 | DeepSeek（`langchain_openai.ChatOpenAI`） |
| 工具 | MCP（`FastMCP` + `MultiServerMCPClient`） |
| 后端 | FastAPI + SSE 流式 |
| 前端 | Vue3 + TypeScript + Element Plus |

## 🤖 多智能体架构

```
START → 画像解析 → 健康评估 ─┬─ 膳食规划 ─┐
                            ├─ 运动规划 ─┼→ 计划合成 → END
                            └─ 作息规划 ─┘
```

| Agent | 职责 | MCP 工具 |
|---|---|---|
| 👤 画像解析 | 输入 → 结构化画像 | — |
| 🩺 健康评估 | BMI + 风险把关 + 就医建议 | calculate_bmi, query_health_knowledge |
| 🥗 膳食规划 | 热量/宏量/三餐 | query_food_nutrition, query_health_knowledge |
| 🏃 运动规划 | 一周运动计划 | get_exercise_guidance, get_weather, query_health_knowledge |
| 🌙 作息规划 | 睡眠/节律 | get_sleep_guidance, query_health_knowledge |
| 📋 计划合成 | 汇总 → 结构化 JSON | — |

## 🛡️ 安全护栏（三层）

1. **规则化预判**（确定性）：`safety.py` 纯 Python 算 BMI + 疾病关键词，产出硬结论。
2. **LLM 判断**：健康评估 Agent 结合工具判断，后端 `max(规则, LLM)` 取更严一档。
3. **免责声明强制注入**：后端无条件覆盖 `meta.disclaimer`，保证 100% 出现。

高风险（BMI < 18.5 或 ≥ 28、有基础疾病）→ `need_medical=true` → 前端红色警示条 + 就医建议。

## 📁 目录结构

```
health-planner/
├── backend/
│   ├── mcp_health_server.py   # FastMCP 6 个 mock 工具
│   ├── health_graph.py        # LangGraph 编排核心（三路并行）
│   ├── state.py               # State + 初始状态
│   ├── prompts.py             # 6 个 system prompt
│   ├── safety.py              # 安全护栏
│   ├── cli.py                 # 命令行版
│   ├── server.py              # FastAPI + SSE
│   └── eval.py                # 评测（单模型 vs 多智能体）
└── frontend/
    └── src/
        ├── views/Planner.vue
        ├── components/        # ProfileForm / ProgressSteps / RiskBanner / PlanCard
        └── api/sse.ts         # fetch + ReadableStream 解析 SSE
```

## 🚀 快速开始

### 1. 后端环境

```bash
cd backend
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.org/simple
cp .env.example .env          # 填入 DEEPSEEK_API_KEY
```

### 2. 命令行跑通（调试用）

```bash
.venv/Scripts/python.exe cli.py
```

### 3. Web 版（FastAPI + SSE）

```bash
.venv/Scripts/python.exe -m uvicorn server:app --reload
```

### 4. 前端

```bash
cd ../frontend
npm install
npm run dev                    # 打开 http://localhost:5173
```

### 5. 评测

```bash
cd ../backend
.venv/Scripts/python.exe eval.py
```

## 📊 评测结果

4 个病例（减脂-超重 / 增肌-正常 / 养生-高血压 / 减脂-低体重）× LLM-as-judge 四维度打分：

| 维度 | 单模型 | 多智能体 | 变化 |
|---|---|---|---|
| 完整性 | 8.8 | 9.8 | ↑ 1.0 |
| 个性化 | 7.8 | 9.5 | ↑ 1.8 |
| 安全性 | 8.5 | 9.5 | ↑ 1.0 |
| 一致性 | 8.5 | 8.0 | ↓ 0.5 |
| **总平均** | **8.4** | **9.2** | **↑ 0.8** |

客观校验（免责声明 / 就医建议 / BMI 一致性）**24 项全 PASS**。

## ⚠️ 注意事项

- **DeepSeek 限流**：三路并行会同时多次调用 LLM，遇到限流（请求卡住）等几分钟自动恢复，或把 `health_graph.py` 的 `PARALLEL` 改为 `False` 退串行。
- **MCP stdio**：`mcp_health_server.py` 内禁止 `print`（会污染 stdio 握手）。
- **Windows venv**：本机 `python` 可能指向别的项目 venv，务必用 `.venv/Scripts/python.exe` 绝对路径。

## 📄 免责声明

本项目的健康计划仅供科普与学习用途，不构成医疗建议。
