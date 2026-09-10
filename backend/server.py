"""健康计划生成助手 - Web 版（FastAPI + SSE 流式 + 记忆）。

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
import memory
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
    "adjust_planner": ("🔧", "计划调整", "final_json"),
}

FINAL_NODES = {"plan_composer", "adjust_planner"}


class ChatRequest(BaseModel):
    session_id: str = ""
    user_id: str = "default"
    message: str = Field(..., min_length=1)


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


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_user_context(user_id: str, profile_input: dict) -> str:
    """把长期记忆（历史画像 + 体重历史）拼成文本，供健康评估引用趋势。"""
    parts = []
    if profile_input:
        parts.append("历史画像：" + json.dumps(profile_input, ensure_ascii=False))
    wh = memory.get_weight_history(user_id)
    if wh:
        parts.append("体重历史：" + "; ".join(f"{x['date']} {x['weight']}kg" for x in wh))
    return "\n".join(parts)


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """SSE 流式对话接口：加载记忆 → 意图路由 → 跑图 → 保存记忆。"""
    graph = app.state.graph
    history = memory.get_history(req.session_id)
    last_plan = memory.get_last_plan(req.user_id) or ""
    profile_input = memory.get_profile(req.user_id) or {}
    user_context = build_user_context(req.user_id, profile_input)
    intent = hg.detect_intent(req.message, last_plan)

    async def event_stream():
        # 1. 意图事件
        yield sse({"node": "intent", "name": "意图判断", "content": intent, "intent": intent})

        initial = initial_state(
            message=req.message,
            session_id=req.session_id,
            user_id=req.user_id,
            history=history,
            last_plan=last_plan,
            profile_input=profile_input,
            user_context=user_context,
            intent=intent,
        )

        new_profile_input = None
        final_json = ""

        # 2. 节点进度 + 最终计划
        async for update in graph.astream(initial, stream_mode="updates"):
            for node_name, node_output in update.items():
                emoji, name, key = NODES.get(node_name, ("🤖", node_name, None))
                if key is None:
                    continue
                content = node_output.get(key, "")
                if not content:
                    continue
                if node_name == "profile_parser":
                    new_profile_input = node_output.get("profile_input")
                is_final = node_name in FINAL_NODES
                if is_final:
                    final_json = content
                yield sse({
                    "node": node_name,
                    "emoji": emoji,
                    "name": name,
                    "content": content,
                    "final": is_final,
                })

        # 3. 保存记忆（短期 + 长期）
        memory.append_message(req.session_id, "user", req.message)
        if final_json:
            memory.append_message(req.session_id, "assistant", final_json)
            memory.save_last_plan(req.user_id, final_json)
            if new_profile_input:
                memory.save_profile(req.user_id, new_profile_input)
                w = new_profile_input.get("weight_kg")
                if w:
                    try:
                        memory.append_weight(req.user_id, float(w))
                    except (TypeError, ValueError):
                        pass
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/memory/{user_id}")
async def get_memory(user_id: str):
    """返回用户长期记忆（画像 + 体重历史），供前端预填表单与展示趋势。"""
    return {
        "profile": memory.get_profile(user_id),
        "weight_history": memory.get_weight_history(user_id),
    }
