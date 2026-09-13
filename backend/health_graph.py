"""健康计划生成助手 - LangGraph 编排核心（含记忆 + 人机协同）。

架构选型（面试常问：为什么是 workflow，而不是让 LLM 自主编排？）
----------------------------------------------------------------
健康场景的评估维度是固定的：画像 → 风险 → 膳食 / 运动 / 作息 → 合成。
少跑一个维度不是"更灵活"，而是漏掉一项安全评估，属于事故。
所以这里刻意选择「稳定 workflow + 节点内 ReAct」，而不是自主多智能体：

  * 编排由 LangGraph 静态 DAG 决定 —— 可审计、可复现、可测试；
  * 每个规划节点内部才是 create_agent 的 ReAct 循环 —— 在那里让模型决定调哪个工具；
  * 风险等级由确定性规则层兜底 —— 不交给 LLM 单方面判断。

《什么样的 Agent 项目才算好项目》原话："多 Agent 不是越多越好……
用稳定 workflow 反而更可控。好的架构不是炫技，而是匹配业务。"

技术亮点：
1. LangGraph —— 图编排：意图路由 + 三路并行 fan-out + 一条带上限的回边
2. MCP —— 为规划节点接入本地健康工具（BMI / 健康知识 / 食物营养 / 运动指南等）
3. 记忆 —— 短期（会话多轮）+ 长期（画像 / 体重历史 / 上一版计划）分层记忆
4. 人机协同 —— 高风险闸门 + 计划确认闸门（LangGraph interrupt + checkpointer）
5. 可观测性 —— trace_id 贯穿请求 / 节点 / 模型 / 工具，见 observability.py

工作流图：
  START ─┬─ generate → 画像解析 → 健康评估 → 风险闸门 ─┬─(中止)→ 就医建议 → END
         │                                            └─(继续)→ 膳食 ──┐
         │                                                     ├ 运动 ──┼→ 计划合成 → 计划确认 ─┬(接受)→ END
         │                                                     └ 作息 ──┘                       └(调整,≤3轮)→ 计划调整 ─┘
         └─ adjust → 计划调整 → 计划确认

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
from langgraph.types import interrupt

from observability import TraceCallbackHandler, timed_node
from prompts import PROMPTS
from safety import (
    DISCLAIMER,
    ensure_disclaimer,
    extract_json,
    parse_risk,
    rule_based_risk,
    stricter_level,
)
from state import MAX_REVISIONS, State

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
# 一个回调处理器实例即可：内部按 run_id 区分并发调用。
_TRACE_HANDLER = TraceCallbackHandler()


def run_config(thread_id: str | None = None) -> dict:
    """统一的调用配置：可观测回调 + 可选的检查点线程。

    回调必须放在 **config 层**，不能挂在模型构造函数上——
    挂在模型上只能覆盖 LLM 调用；create_agent 内部 ToolNode 发起的
    工具调用是另一个 run，拿不到模型上的局部回调（实测工具事件数为 0）。
    放在 config 上，整棵调用树（节点 / 模型 / 工具 / 链）都会继承它。

    注意不要两处都挂：那样每个 LLM 调用会被记两次，token 统计翻倍。
    """
    cfg: dict = {"callbacks": [_TRACE_HANDLER]}
    if thread_id:
        cfg["configurable"] = {"thread_id": thread_id}
    return cfg


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


# ---------- 2.5 人机协同闸门（Human-in-the-loop） ----------
# 《什么样的 Agent 项目才算好项目》第六节：好的 Agent 不是所有事情都自动做到底，
# 在关键节点上它应该主动让人确认；高风险操作失败后要转人工。
# 企业真正关心的不是 Agent 多聪明，而是它能不能「可控地」完成任务。
#
# 实现依赖 LangGraph 的 interrupt + checkpointer：
#   interrupt() 抛出一个特殊信号，把当前状态存进 checkpointer 并结束本次调用；
#   之后用同一个 thread_id 调 Command(resume=值)，节点会从头重跑，
#   此时 interrupt() 直接返回那个 resume 值。
# 因此闸门节点在 interrupt() 之前不能有任何副作用，否则重跑时会重复执行。

_ABORT_WORDS = (
    "abort", "cancel", "reject", "false", "stop", "no",
    "中止", "取消", "暂不", "先不", "看医生", "不继续",
)
_GO_WORDS = (
    "continue", "accept", "true", "yes", "ok", "proceed",
    "继续", "生成", "接受", "确认", "好的",
)
_REVISE_WORDS = (
    "revise", "adjust", "modify", "change",
    "调整", "修改", "重来", "不满意", "改", "换",
)


def _decision_text(decision) -> str:
    """把恢复值归一化成小写文本；兼容裸字符串与 {"decision": ...} 两种前端传法。"""
    if isinstance(decision, dict):
        for key in ("decision", "value", "choice", "answer"):
            if decision.get(key):
                return str(decision[key]).strip().lower()
        return str(decision).strip().lower()
    return str(decision).strip().lower()


def _matches(text: str, words: tuple) -> bool:
    return any(w in text for w in words)


def risk_gate_node(state: State) -> dict:
    """高风险闸门：评估判定需要就医时，先停下来让用户确认。

    识别不出用户意图时按「中止」处理（fail-closed）。
    这个方向是刻意的：在需要就医的场景里，误判成「继续」的代价远大于误判成「中止」。
    """
    if not state.get("need_medical"):
        return {"risk_ack": True, "aborted": False}

    decision = interrupt({
        "type": "risk_confirmation",
        "title": "需要就医提示确认",
        "question": "健康评估提示你需要就医。是否仍要继续生成健康计划？",
        "risk_level": state.get("risk_level", "unknown"),
        "bmi": rule_based_risk(state.get("profile_input") or {}).get("bmi"),
        "options": [
            {"value": "continue", "label": "我已了解，继续生成", "style": "default"},
            {"value": "abort", "label": "先去看医生，暂不生成", "style": "danger"},
        ],
        "hint": "选择中止不会生成计划，只会给出就医建议。",
    })

    text = _decision_text(decision)
    if _matches(text, _ABORT_WORDS):
        return {"risk_ack": False, "aborted": True}
    if _matches(text, _GO_WORDS):
        return {"risk_ack": True, "aborted": False}
    return {"risk_ack": False, "aborted": True}  # fail-closed


def confirm_gate_node(state: State) -> dict:
    """计划确认闸门：产出计划后停下来问用户接不接受。

    与风险闸门的区别是这个闸门会形成一条真实回边
    （confirm_gate → adjust_planner → confirm_gate），
    所以必须有 MAX_REVISIONS 上限——这就是「防死循环」：
    没有上限时用户可以无限要求调整，token 成本也随之无上限。
    """
    revisions = state.get("revision_count", 0)
    if revisions >= MAX_REVISIONS:
        # 已到上限：不再询问，直接放行结束
        return {"revision_request": ""}

    decision = interrupt({
        "type": "plan_confirmation",
        "title": "计划确认",
        "question": "这份计划是否符合预期？",
        "revision_count": revisions,
        "max_revisions": MAX_REVISIONS,
        "options": [
            {"value": "accept", "label": "接受这份计划", "style": "primary"},
            {"value": "revise", "label": "需要调整", "style": "default"},
        ],
        "hint": f"还可以调整 {MAX_REVISIONS - revisions} 轮。",
    })

    text = _decision_text(decision)
    feedback = ""
    if isinstance(decision, dict):
        feedback = str(decision.get("feedback") or decision.get("comment") or "").strip()

    if _matches(text, _REVISE_WORDS):
        new_message = feedback or state.get("message", "")
        return {
            "revision_count": revisions + 1,
            "revision_request": new_message,
            "message": new_message,
        }
    return {"revision_request": ""}


def abort_node(state: State) -> dict:
    """用户在风险闸门选择中止：不生成任何计划，只给出就医建议。

    这条分支本身就是护栏的一部分——系统提供「不做事」的选项，
    而不是无论用户怎么选都要产出一份计划。
    """
    obj = {
        "meta": {"aborted": True, "abort_reason": "用户在风险闸门选择先就医"},
        "summary": "已按你的选择停止生成健康计划。",
        "profile": {"bmi": rule_based_risk(state.get("profile_input") or {}).get("bmi")},
        "risk": {"level": state.get("risk_level", "high"), "need_medical": True},
        "next_steps": [
            "带上近期体检报告或既往病史，先去正规医院相应科室就诊",
            "由医生评估之后再决定是否适合开始减脂 / 增肌 / 运动计划",
            "如果已经拿到医生的意见，可以回来重新生成，并告知医生给出的限制",
        ],
        "assessment": state.get("assessment", ""),
    }
    obj = ensure_disclaimer(obj)
    final = json.dumps(obj, ensure_ascii=False, indent=2)
    show("🛑 用户中止 → 就医建议", final, preview=600)
    return {"final_json": final, "aborted": True}


def plan_fanout_node(state: State) -> dict:
    """三路规划的分发点（空节点）。

    LangGraph 的 add_conditional_edges 不支持「一个分支映射到多个节点」：
    传入 list 会被静默接受，但 compile 时抛 TypeError: unhashable type: 'list'
    （langgraph 1.2.11 实测）。所以这里显式放一个空节点承担分发——
    条件边只负责判断「是否中止」，分发交给普通边完成。
    """
    return {}


def route_after_risk_gate(state: State) -> str:
    return "abort" if state.get("aborted") else "plan"


def route_after_confirm(state: State) -> str:
    return "revise" if state.get("revision_request") else "accept"


# ---------- 2.6 中断处理（非交互入口共用） ----------
# 兜底上限：风险闸门最多触发 1 次，确认闸门最多 MAX_REVISIONS + 1 次，
# 正常情况下不会到这里；留着是防止改图时不小心造出无限请求人工的环。
MAX_INTERRUPTS = 8

# 无人值守时的默认应答。评测必须能跑完，所以取「继续 / 接受」；
# 交互入口（server.py / cli.py）不走这里——那里必须把决定权交给人。
AUTO_ANSWER = {"risk_confirmation": "continue", "plan_confirmation": "accept"}


def extract_interrupt(result) -> dict | None:
    """从 graph 调用结果里取出中断载荷；没有中断则返回 None。

    LangGraph 把中断放在结果的 `__interrupt__` 键下，值是一个元组，
    元素为 Interrupt 对象，真正的内容在它的 .value 上。
    """
    if not isinstance(result, dict):
        return None
    raw = result.get("__interrupt__")
    if not raw:
        return None
    first = raw[0] if isinstance(raw, (tuple, list)) else raw
    return getattr(first, "value", first)


async def run_to_completion(graph, initial, config=None, auto_answer: bool = True):
    """跑图直到结束，遇到闸门时按 AUTO_ANSWER 自动应答。

    这是给**非交互入口**（eval.py 评测脚本）用的。
    返回 (最终状态, 触发过的中断载荷列表)——后者可以统计
    "这套评测集里有多少用例会触发人工确认"，本身就是个可汇报的指标。
    """
    from langgraph.types import Command

    state = await graph.ainvoke(initial, config=config)
    interrupts: list = []
    for _ in range(MAX_INTERRUPTS):
        payload = extract_interrupt(state)
        if payload is None:
            break
        interrupts.append(payload)
        if not auto_answer:
            break
        answer = AUTO_ANSWER.get(payload.get("type"), "accept")
        state = await graph.ainvoke(Command(resume=answer), config=config)
    return state, interrupts


# ---------- 3. 带工具的 Agent（在 build_graph 里用 MCP 工具初始化） ----------
ASSESSOR_AGENT = None
DIET_AGENT = None
EXERCISE_AGENT = None
LIFESTYLE_AGENT = None


def build_graph(tools, checkpointer=None):
    """构建并编译图。

    checkpointer 是人机协同的前提：interrupt 会把图状态存进 checkpointer，
    之后用同一个 thread_id 调 Command(resume=...) 才能接着跑。
    server.py 传 AsyncSqliteSaver（落盘，服务重启后仍可恢复）；
    cli.py / 测试传 MemorySaver（仅进程内）。
    """
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
    g.add_node("profile_parser", timed_node("profile_parser", profile_parser_node))
    g.add_node("health_assessor", timed_node("health_assessor", health_assessor_node))
    g.add_node("risk_gate", timed_node("risk_gate", risk_gate_node))
    g.add_node("abort", timed_node("abort", abort_node))
    g.add_node("plan_fanout", timed_node("plan_fanout", plan_fanout_node))
    g.add_node("diet_planner", timed_node("diet_planner", diet_planner_node))
    g.add_node("exercise_planner", timed_node("exercise_planner", exercise_planner_node))
    g.add_node("lifestyle_planner", timed_node("lifestyle_planner", lifestyle_planner_node))
    g.add_node("plan_composer", timed_node("plan_composer", plan_composer_node))
    g.add_node("confirm_gate", timed_node("confirm_gate", confirm_gate_node))
    g.add_node("adjust_planner", timed_node("adjust_planner", adjust_planner_node))

    # 意图路由：首次生成走完整流程，追问调整直接进调整节点
    g.add_conditional_edges(
        START, lambda s: s["intent"], {"generate": "profile_parser", "adjust": "adjust_planner"}
    )
    g.add_edge("profile_parser", "health_assessor")

    # 高风险闸门：需要就医时先停下来让人确认，确认继续才进三路规划
    g.add_edge("health_assessor", "risk_gate")
    g.add_conditional_edges(
        "risk_gate", route_after_risk_gate, {"plan": "plan_fanout", "abort": "abort"}
    )
    g.add_edge("abort", END)

    if PARALLEL:
        g.add_edge("plan_fanout", "diet_planner")
        g.add_edge("plan_fanout", "exercise_planner")
        g.add_edge("plan_fanout", "lifestyle_planner")
        g.add_edge("diet_planner", "plan_composer")
        g.add_edge("exercise_planner", "plan_composer")
        g.add_edge("lifestyle_planner", "plan_composer")
    else:
        g.add_edge("plan_fanout", "diet_planner")
        g.add_edge("diet_planner", "exercise_planner")
        g.add_edge("exercise_planner", "lifestyle_planner")
        g.add_edge("lifestyle_planner", "plan_composer")

    # 计划确认闸门 + 带上限的回边（防死循环）
    g.add_edge("plan_composer", "confirm_gate")
    g.add_conditional_edges(
        "confirm_gate", route_after_confirm, {"accept": END, "revise": "adjust_planner"}
    )
    g.add_edge("adjust_planner", "confirm_gate")
    return g.compile(checkpointer=checkpointer)
