"""命令行版健康计划生成助手（对话式，支持多轮追问 + 分层记忆 + 人机协同）。

运行：
  .venv/Scripts/python.exe cli.py

命令行是验证「人机协同」最省事的入口：
不需要前端，确认闸门会直接在终端里把问题问出来。
"""

import asyncio
import os
import sys
import uuid

# Windows 控制台默认 GBK，emoji 会报 UnicodeEncodeError；强制 UTF-8 输出
for _s in (sys.stdout, sys.stderr):
    if getattr(_s, "encoding", "").lower() not in ("utf-8", "utf8"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import health_graph as hg
import memory
from state import initial_state

HERE = os.path.dirname(os.path.abspath(__file__))


def build_user_context(user_id: str, profile_input: dict) -> str:
    parts = []
    if profile_input:
        parts.append("历史画像：" + str(profile_input))
    wh = memory.get_weight_history(user_id)
    if wh:
        parts.append("体重历史：" + "; ".join(f"{x['date']} {x['weight']}kg" for x in wh))
    return "\n".join(parts)


def ask_human(payload: dict) -> dict:
    """在终端里渲染一个确认闸门，返回结构化的用户决定。

    分类工作交给闸门节点自己做（它已经有中英文关键词规则），
    这里只负责"把用户输入原样带回去"——规则只有一份，不会两处走偏。
    """
    print("\n" + "!" * 60)
    print(f"  ⏸️  {payload.get('title', '需要你确认')}")
    print("!" * 60)
    print(payload.get("question", ""))

    if payload.get("type") == "risk_confirmation":
        bmi = payload.get("bmi")
        line = f"  风险等级：{payload.get('risk_level', 'unknown')}"
        if bmi is not None:
            line += f"    BMI：{bmi}"
        print(line)
    if payload.get("type") == "plan_confirmation":
        print(f"  已调整 {payload.get('revision_count', 0)} / {payload.get('max_revisions', 0)} 轮")

    options = payload.get("options") or []
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt.get('label', opt.get('value'))}")
    if payload.get("hint"):
        print(f"  （{payload['hint']}）")

    raw = input("请选择序号，或直接输入你的意见：").strip()

    if raw.isdigit() and 1 <= int(raw) <= len(options):
        value = options[int(raw) - 1]["value"]
        if value == "revise":
            # 选了"需要调整"却没说改什么，adjust_planner 只能拿到原消息，
            # 结果就是生成一份一模一样的计划——必须追问一句
            return {"decision": value, "feedback": input("请写下你的调整意见：").strip()}
        return {"decision": value, "feedback": ""}

    # 用户直接打字：既当决定，也当调整意见（confidence 由闸门节点自己判定）
    return {"decision": raw, "feedback": raw}


async def main():
    print("🧘 健康计划生成助手（LangGraph + MCP + 分层记忆 + 人机协同）")
    print("-" * 60)

    client = MultiServerMCPClient({
        "health": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["mcp_health_server.py"],
            "cwd": HERE,
        }
    })
    tools = await client.get_tools()
    print(f"✅ 已通过 MCP 加载 {len(tools)} 个工具：{[t.name for t in tools]}")

    # 有 checkpointer 才能 interrupt —— 没有它，闸门节点会直接报错
    graph = hg.build_graph(tools, checkpointer=MemorySaver())

    session_id = uuid.uuid4().hex[:8]
    user_id = input("用户 ID（回车默认 default）：").strip() or "default"

    print(f"\n会话已建立（session={session_id}）。")
    print("输入身体信息生成计划，之后可继续追问调整（如「把运动强度调低」）。")
    print("示例：我男28岁175cm82kg想减脂；输入 q 退出。\n")

    while True:
        message = input("👤 你：").strip()
        if not message:
            continue
        if message.lower() == "q":
            print("👋 再见！")
            break

        history = memory.get_history(session_id)
        last_plan = memory.get_last_plan(user_id) or ""
        profile_input = memory.get_profile(user_id) or {}
        user_context = build_user_context(user_id, profile_input)
        intent = hg.detect_intent(message, last_plan)
        print(f"\n🧭 意图：{'首次生成' if intent == 'generate' else '追问调整'}")

        initial = initial_state(
            message=message,
            session_id=session_id,
            user_id=user_id,
            history=history,
            last_plan=last_plan,
            profile_input=profile_input,
            user_context=user_context,
            intent=intent,
        )

        # 每一轮用新的 thread_id：复用同一个 id 会让上一轮的状态
        # （比如上一轮的 aborted / final_json）被带进这一轮
        config = hg.run_config(f"{session_id}:{uuid.uuid4().hex[:8]}")
        state = await graph.ainvoke(initial, config=config)

        # 人机协同：碰到闸门就把问题抛到终端，拿到答复后接着跑同一条线程
        while True:
            payload = hg.extract_interrupt(state)
            if payload is None:
                break
            answer = ask_human(payload)
            state = await graph.ainvoke(Command(resume=answer), config=config)

        final_json = state.get("final_json", "")

        # 保存记忆（短期 + 长期）
        memory.append_message(session_id, "user", message)
        if final_json:
            memory.append_message(session_id, "assistant", final_json)
            memory.save_last_plan(user_id, final_json)
            new_profile = state.get("profile_input") or {}
            if intent == "generate" and new_profile:
                memory.save_profile(user_id, new_profile)
                w = new_profile.get("weight_kg")
                if w:
                    try:
                        memory.append_weight(user_id, float(w))
                    except (TypeError, ValueError):
                        pass

        print("\n" + "=" * 60)
        print("  📋 健康计划（JSON）")
        print("=" * 60)
        print(final_json)
        print("\n（继续输入可追问调整；输入 q 退出）\n")


if __name__ == "__main__":
    asyncio.run(main())
