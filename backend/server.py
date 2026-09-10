"""健康计划生成助手 - Web 版（FastAPI + SSE 流式）。

运行：
  .venv/Scripts/python.exe -m uvicorn server:app --reload
  前端在 frontend/ 目录 npm run dev 后访问 http://localhost:5173
"""

import json
import os
import sys
from contextlib import asynccontextmanager

# Windows 控制台默认 GBK，emoji 会报 UnicodeEncodeError；强制 UTF-8 输出
for _s in (sys.stdout, sys.stderr):
    if getattr(_s, "encoding", "").lower() not in ("utf-8", "utf8"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import BaseModel, Field

import health_graph as hg
from state import initial_state

HERE = os.path.dirname(os.path.abspath(__file__))

# 节点名 → (emoji, 中文名, 产出的 state key)
NODES = {
    "profile_parser": ("👤", "画像解析", "profile"),
    "health_assessor": ("🩺", "健康评估", "assessment"),
    "diet_planner": ("🥗", "膳食规划", "diet_plan"),
    "exercise_planner": ("🏃", "运动规划", "exercise_plan"),
    "lifestyle_planner": ("🌙", "作息规划", "lifestyle_plan"),
    "plan_composer": ("📋", "计划合成", "final_json"),
}


class PlanRequest(BaseModel):
    gender: str = "男"
    age: int = Field(28, ge=1, le=120)
    height_cm: float = Field(175, ge=100, le=250)
    weight_kg: float = Field(82, ge=30, le=300)
    goal: str = "减脂"
    diseases: list[str] = []
    city: str = "北京"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时加载 MCP 工具 + 构建图（复用，不每次请求都建）。"""
    client = MultiServerMCPClient({
        "health": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["mcp_health_server.py"],
            "cwd": HERE,
        }
    })
    tools = await client.get_tools()
    app.state.graph = hg.build_graph(tools)
    print(f"✅ MCP 工具加载完成：{[t.name for t in tools]}")
    yield
    # MCP 子进程随服务进程退出而清理


app = FastAPI(title="健康计划生成助手", lifespan=lifespan)


@app.post("/api/generate")
async def generate(req: PlanRequest):
    """SSE 流式接口：每完成一个节点就推一条消息。"""
    graph = app.state.graph
    profile_input = req.model_dump()

    async def event_stream():
        async for update in graph.astream(initial_state(profile_input), stream_mode="updates"):
            for node_name, node_output in update.items():
                emoji, name, key = NODES.get(node_name, ("🤖", node_name, None))
                if key is None:
                    continue
                content = node_output.get(key, "")
                if not content:
                    continue
                payload = {
                    "node": node_name,
                    "emoji": emoji,
                    "name": name,
                    "content": content,
                    "final": node_name == "plan_composer",
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}
