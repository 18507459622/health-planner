"""健康计划生成助手 - LangGraph 编排核心（含记忆）。

技术亮点：
1. LangGraph —— 用「图」编排多智能体工作流：意图路由 + 三路并行 fan-out
2. MCP —— 为多个智能体接入本地健康工具（BMI 计算 / 健康知识 / 食物营养 / 运动指南等）
3. 记忆 —— 短期（会话多轮）+ 长期（画像 / 体重历史 / 上一版计划）分层记忆

工作流图：
  START ─┬─ generate → 画像解析 → 健康评估 ─┬─ 膳食规划 ─┐
         │                                ├─ 运动规划 ─┼→ 计划合成 → END
         │                                └─ 作息规划 ─┘
         └─ adjust   → 计划调整（基于上一版计划 + 追问）──────────→ END

运行：.venv/Scripts/python.exe cli.py（命令行）或 server.py（Web）
"""

import json
import os
import re
import sys

# Windows 控制台默认 GBK，emoji 会报 UnicodeEncodeError；强制 UTF-8 输出
for _s in (sys.stdout, sys.stderr):
    if getattr(_s, "encoding", "").lower() not in ("utf-8", "utf8"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from prompts import PROMPTS
from safety import (
    DISCLAIMER,
    ensure_disclaimer,
    extract_json,
    parse_risk,
    rule_based_risk,
    stricter_level,
)
from state import State

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = "https://api.deepseek.com"
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
PARALLEL = True  # 是否三路并行（False 退化为串行，规避限流）

if not API_KEY:
    print("❌ 未找到 DEEPSEEK_API_KEY，请先配置 .env 或环境变量")
    sys.exit(1)


# ---------- 0. 意图判断（规则路由：首次生成 vs 追问调整） ----------
_INTENT_PATTERN = re.compile(r"(身高|体重|\d+\s*(cm|kg|公斤|斤)|减脂|增肌|养生)")


def detect_intent(message: str, last_plan: str) -> str:
    """规则判断意图：含画像/目标关键词 → 首次生成；有上一版计划 → 追问调整。"""
    if _INTENT_PATTERN.search(message):
        return "generate"
    if last_plan:
        return "adjust"
    return "generate"


# ---------- 1. 大模型工厂 ----------
def make_llm(temperature: float = 0.7, json_mode: bool = False) -> ChatOpenAI:
    kwargs: dict = {"max_retries": 3}
    if json_mode:
        # DeepSeek JSON Output 模式：prompt 里必须出现「json」字样（见 prompts.py）
        kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
    return ChatOpenAI(
        model=MODEL, api_key=API_KEY, base_url=BASE_URL, temperature=temperature, **kwargs
    )


def show(title: str, content: str, preview: int | None = None) -> None:
    """打印某个角色的产出（带分隔线和标题），让协作过程可视化。"""
    print("\n" + "─" * 60)
    print(f"  {title}")
    print("─" * 60)
    if preview and len(content) > preview:
        print(content[:preview])
        print(f"  ...（共 {len(content)} 字，已截断）")
    else:
        print(content)


# ---------- 2. 各节点 ----------
def profile_parser_node(state: State) -> dict:
    print("\n👤 画像解析 工作中...")
    llm = make_llm(0.2, json_mode=True)
    resp = llm.invoke([SystemMessage(PROMPTS["profile_parser"]), HumanMessage(state["message"])])
    obj = extract_json(resp.content)
    if obj is None:
        obj = {"raw": state["message"]}
    profile_input = {
        "gender": obj.get("gender") or "未知",
        "age": obj.get("age"),
        "height_cm": obj.get("height_cm"),
        "weight_kg": obj.get("weight_kg"),
        "goal": obj.get("goal") or "未知",
        "diseases": obj.get("diseases") or [],
        "city": obj.get("city") or "未知",
    }
    profile_text = json.dumps(obj, ensure_ascii=False, indent=2)
    show("👤 画像解析 → 结构化画像", profile_text)
    return {"profile": profile_text, "profile_input": profile_input}


async def health_assessor_node(state: State) -> dict:
    print("\n🩺 健康评估 工作中（计算 BMI + 风险把关）...")
    # 第一层护栏：确定性规则预判，注入 prompt 供 LLM 参考（取更严者兜底）
    rule = rule_based_risk(state["profile_input"])
    prompt = (
        f"用户画像：\n{state['profile']}\n\n"
        f"【用户历史记录】\n{state['user_context'] or '无'}\n\n"
        f"【确定性预判结果（供参考，与你的判断冲突时以更严格者为准）】\n"
        f"BMI={rule['bmi']}，风险等级={rule['risk_level']}，是否需要就医={rule['need_medical']}\n\n"
        f"请调用 calculate_bmi 计算 BMI、query_health_knowledge 查询相关健康知识，输出评估报告。"
    )
    result = await ASSESSOR_AGENT.ainvoke({"messages": [HumanMessage(prompt)]})
    report = result["messages"][-1].content
    llm_level, llm_need = parse_risk(report)
    level = stricter_level(rule["risk_level"], llm_level)
    need_medical = rule["need_medical"] or llm_need
    show("🩺 健康评估 → 报告", report)
    return {"assessment": report, "risk_level": level, "need_medical": need_medical}


async def diet_planner_node(state: State) -> dict:
    print("\n🥗 膳食规划 工作中...")
    prompt = (
        f"用户画像：\n{state['profile']}\n\n"
        f"健康评估：\n{state['assessment']}\n\n"
        f"风险等级={state['risk_level']}，是否需要就医={state['need_medical']}\n\n"
        f"请调用 query_food_nutrition / query_health_knowledge 工具，制定膳食计划。"
    )
    result = await DIET_AGENT.ainvoke({"messages": [HumanMessage(prompt)]})
    report = result["messages"][-1].content
    show("🥗 膳食规划 → 计划", report, preview=400)
    return {"diet_plan": report}


async def exercise_planner_node(state: State) -> dict:
    print("\n🏃 运动规划 工作中...")
    prompt = (
        f"用户画像：\n{state['profile']}\n\n"
        f"健康评估：\n{state['assessment']}\n\n"
        f"风险等级={state['risk_level']}，是否需要就医={state['need_medical']}\n\n"
        f"请调用 get_exercise_guidance / get_weather / query_health_knowledge 工具，制定一周运动计划。"
    )
    result = await EXERCISE_AGENT.ainvoke({"messages": [HumanMessage(prompt)]})
    report = result["messages"][-1].content
    show("🏃 运动规划 → 计划", report, preview=400)
    return {"exercise_plan": report}


async def lifestyle_planner_node(state: State) -> dict:
    print("\n🌙 作息规划 工作中...")
    prompt = (
        f"用户画像：\n{state['profile']}\n\n"
        f"健康目标：{state['profile_input'].get('goal', '未知')}\n\n"
        f"请调用 get_sleep_guidance / query_health_knowledge 工具，制定作息与生活方式建议。"
    )
    result = await LIFESTYLE_AGENT.ainvoke({"messages": [HumanMessage(prompt)]})
    report = result["messages"][-1].content
    show("🌙 作息规划 → 建议", report, preview=400)
    return {"lifestyle_plan": report}


def plan_composer_node(state: State) -> dict:
    print("\n📋 计划合成 工作中（输出 JSON）...")
    llm = make_llm(0.3, json_mode=True)
    prompt = (
        f"请将以下各部分信息整合为一个 JSON 格式的完整健康计划：\n\n"
        f"【用户画像】\n{state['profile']}\n\n"
        f"【健康评估】风险等级={state['risk_level']}，是否需要就医={state['need_medical']}\n"
        f"{state['assessment']}\n\n"
        f"【膳食计划】\n{state['diet_plan']}\n\n"
        f"【运动计划】\n{state['exercise_plan']}\n\n"
        f"【作息计划】\n{state['lifestyle_plan']}"
    )
    obj = None
    raw = ""
    for _ in range(2):  # 最多尝试两次
        resp = llm.invoke([SystemMessage(PROMPTS["plan_composer"]), HumanMessage(prompt)])
        raw = resp.content
        obj = extract_json(raw)
        if obj is not None:
            break
    if obj is None:
        # 降级兜底：返回带免责声明的原始文本
        obj = {"meta": {"disclaimer": DISCLAIMER}, "raw_plan": raw}

    # 第三层护栏：免责声明强制注入 + 风险结论回填（保证与确定性规则一致）
    obj = ensure_disclaimer(obj)
    rule = rule_based_risk(state["profile_input"])
    obj.setdefault("profile", {})
    if rule["bmi"] is not None:
        obj["profile"]["bmi"] = rule["bmi"]
    obj.setdefault("risk", {})
    obj["risk"]["level"] = state["risk_level"]
    obj["risk"]["need_medical"] = state["need_medical"]

    final = json.dumps(obj, ensure_ascii=False, indent=2)
    show("📋 计划合成 → 最终 JSON", final, preview=600)
    return {"final_json": final}


def adjust_planner_node(state: State) -> dict:
    print("\n🔧 计划调整 工作中（基于上一版计划）...")
    llm = make_llm(0.4, json_mode=True)
    # 短期记忆：最近几轮对话，帮助理解连续追问的上下文
    history_text = "\n".join(
        f"{m['role']}: {str(m['content'])[:200]}" for m in state["history"][-6:]
    )
    prompt = (
        f"【最近对话】\n{history_text or '无'}\n\n"
        f"上一版健康计划（JSON）：\n{state['last_plan']}\n\n"
        f"用户的调整需求：{state['message']}"
    )
    obj = None
    raw = ""
    for _ in range(2):
        resp = llm.invoke([SystemMessage(PROMPTS["adjust_planner"]), HumanMessage(prompt)])
        raw = resp.content
        obj = extract_json(raw)
        if obj is not None:
            break
    if obj is None:
        obj = extract_json(state["last_plan"]) or {"raw": raw}

    # 免责声明强制注入 + 风险结论回填（仅当有画像信息时）
    obj = ensure_disclaimer(obj)
    rule = rule_based_risk(state["profile_input"])
    if rule["bmi"] is not None or rule["diseases"]:
        obj.setdefault("risk", {})
        obj["risk"]["level"] = rule["risk_level"]
        obj["risk"]["need_medical"] = rule["need_medical"]

    final = json.dumps(obj, ensure_ascii=False, indent=2)
    show("🔧 计划调整 → 最终 JSON", final, preview=600)
    return {"final_json": final}


# ---------- 3. 带工具的 Agent（在 build_graph 里用 MCP 工具初始化） ----------
ASSESSOR_AGENT = None
DIET_AGENT = None
EXERCISE_AGENT = None
LIFESTYLE_AGENT = None


def build_graph(tools):
    global ASSESSOR_AGENT, DIET_AGENT, EXERCISE_AGENT, LIFESTYLE_AGENT
    t = {x.name: x for x in tools}
    ASSESSOR_AGENT = create_agent(
        make_llm(0.2), [t["calculate_bmi"], t["query_health_knowledge"]],
        system_prompt=PROMPTS["health_assessor"],
    )
    DIET_AGENT = create_agent(
        make_llm(0.6), [t["query_food_nutrition"], t["query_health_knowledge"]],
        system_prompt=PROMPTS["diet_planner"],
    )
    EXERCISE_AGENT = create_agent(
        make_llm(0.6), [t["get_exercise_guidance"], t["get_weather"], t["query_health_knowledge"]],
        system_prompt=PROMPTS["exercise_planner"],
    )
    LIFESTYLE_AGENT = create_agent(
        make_llm(0.6), [t["get_sleep_guidance"], t["query_health_knowledge"]],
        system_prompt=PROMPTS["lifestyle_planner"],
    )

    g = StateGraph(State)
    g.add_node("profile_parser", profile_parser_node)
    g.add_node("health_assessor", health_assessor_node)
    g.add_node("diet_planner", diet_planner_node)
    g.add_node("exercise_planner", exercise_planner_node)
    g.add_node("lifestyle_planner", lifestyle_planner_node)
    g.add_node("plan_composer", plan_composer_node)
    g.add_node("adjust_planner", adjust_planner_node)

    # 意图路由：首次生成走完整流程，追问调整走调整节点
    g.add_conditional_edges(
        START, lambda s: s["intent"], {"generate": "profile_parser", "adjust": "adjust_planner"}
    )
    g.add_edge("profile_parser", "health_assessor")

    if PARALLEL:
        g.add_edge("health_assessor", "diet_planner")
        g.add_edge("health_assessor", "exercise_planner")
        g.add_edge("health_assessor", "lifestyle_planner")
        g.add_edge("diet_planner", "plan_composer")
        g.add_edge("exercise_planner", "plan_composer")
        g.add_edge("lifestyle_planner", "plan_composer")
    else:
        g.add_edge("health_assessor", "diet_planner")
        g.add_edge("diet_planner", "exercise_planner")
        g.add_edge("exercise_planner", "lifestyle_planner")
        g.add_edge("lifestyle_planner", "plan_composer")

    g.add_edge("plan_composer", END)
    g.add_edge("adjust_planner", END)
    return g.compile()
