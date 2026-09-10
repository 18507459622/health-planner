"""命令行版健康计划生成助手（方便调试全链路）。

运行：
  .venv/Scripts/python.exe cli.py
"""

import asyncio
import json
import os
import sys

# Windows 控制台默认 GBK，emoji 会报 UnicodeEncodeError；强制 UTF-8 输出
for _s in (sys.stdout, sys.stderr):
    if getattr(_s, "encoding", "").lower() not in ("utf-8", "utf8"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

from langchain_mcp_adapters.client import MultiServerMCPClient

import health_graph as hg
from state import initial_state

HERE = os.path.dirname(os.path.abspath(__file__))


def ask_float(prompt: str, default: float) -> float:
    raw = input(prompt).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


async def main():
    print("🧘 健康计划生成助手（LangGraph + MCP 多智能体）")
    print("-" * 60)

    # 连接 MCP 服务器，加载工具
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

    # 收集输入（都有默认值，方便快速回车跑通）
    gender = input("性别（男/女，回车默认：男）：").strip() or "男"
    age = int(input("年龄（回车默认：28）：").strip() or "28")
    height = ask_float("身高 cm（回车默认：175）：", 175)
    weight = ask_float("体重 kg（回车默认：82）：", 82)
    goal = input("健康目标（减脂/增肌/养生，回车默认：减脂）：").strip() or "减脂"
    diseases_raw = input("基础疾病（逗号分隔，无则回车）：").strip()
    diseases = [d.strip() for d in diseases_raw.split(",") if d.strip()] if diseases_raw else []
    city = input("所在城市（回车默认：北京）：").strip() or "北京"

    profile_input = {
        "gender": gender, "age": age, "height_cm": height, "weight_kg": weight,
        "goal": goal, "diseases": diseases, "city": city,
    }

    print("\n🚀 多智能体流水线启动...")
    result = await graph.ainvoke(initial_state(profile_input))

    print("\n" + "=" * 60)
    print("  📋 最终健康计划（JSON）")
    print("=" * 60)
    print(result["final_json"])

    # 保存
    out_dir = os.path.join(HERE, "output")
    os.makedirs(out_dir, exist_ok=True)
    filename = os.path.join(out_dir, f"{goal}-计划.json")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(result["final_json"])
    print(f"\n✅ 完成！计划已保存到：{filename}")


if __name__ == "__main__":
    asyncio.run(main())
