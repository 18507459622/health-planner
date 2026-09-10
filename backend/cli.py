"""命令行版健康计划生成助手（对话式，支持多轮追问 + 记忆）。

运行：
  .venv/Scripts/python.exe cli.py
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


async def main():
    print("🧘 健康计划生成助手（LangGraph + MCP + 分层记忆）")
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
    graph = hg.build_graph(tools)

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
        result = await graph.ainvoke(initial)
        final_json = result.get("final_json", "")

        # 保存记忆（短期 + 长期）
        memory.append_message(session_id, "user", message)
        if final_json:
            memory.append_message(session_id, "assistant", final_json)
            memory.save_last_plan(user_id, final_json)
            new_profile = result.get("profile_input") or {}
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
