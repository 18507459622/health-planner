"""健康计划生成助手 - Web 版（FastAPI + SSE 流式 + 分层记忆 + 人机协同 + 可观测性）。

运行：
  .venv/Scripts/python.exe -m uvicorn server:app --reload
  前端在 frontend/ 目录 npm run dev 后访问 http://localhost:5173

接口一览：
  POST /api/chat              SSE 流式对话（可能在确认闸门处暂停）
  POST /api/chat/resume       恢复一个暂停中的图
  POST /api/feedback          记录用户是否接受最终结果
  GET  /api/memory/{user_id}  长期记忆（画像 + 体重历史）
  GET  /api/metrics           聚合指标（延迟 / token 成本 / 工具成功率 / 失败归因）
  GET  /api/traces            最近请求列表
  GET  /api/trace/{trace_id}  单次请求的完整事件链
  GET  /api/health            存活探针
"""

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

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

import health_graph as hg
import memory
import observability as obs
from state import initial_state

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_DB = os.path.join(HERE, "data", "checkpoints.sqlite")

# 节点名 → (emoji, 中文名, 产出的 state key)
NODES = {
    "profile_parser": ("👤", "画像解析", "profile"),
    "health_assessor": ("🩺", "健康评估", "assessment"),
    "diet_planner": ("🥗", "膳食规划", "diet_plan"),
    "exercise_planner": ("🏃", "运动规划", "exercise_plan"),
    "lifestyle_planner": ("🌙", "作息规划", "lifestyle_plan"),
    "plan_composer": ("📋", "计划合成", "final_json"),
    "adjust_planner": ("🔧", "计划调整", "final_json"),
    "abort": ("🛑", "已中止（就医建议）", "final_json"),
}

FINAL_NODES = {"plan_composer", "adjust_planner", "abort"}


class ChatRequest(BaseModel):
    session_id: str = ""
    user_id: str = "default"
    message: str = Field(..., min_length=1)


class ResumeRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    decision: str = Field(..., min_length=1)
    feedback: str = ""
    session_id: str = ""
    user_id: str = "default"


class FeedbackRequest(BaseModel):
    trace_id: str = Field(..., min_length=1)
    accepted: bool
    comment: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时加载 MCP 工具 + 建图；检查点落盘，服务重启后仍可恢复中断。"""
    os.makedirs(os.path.dirname(CHECKPOINT_DB), exist_ok=True)
    client = MultiServerMCPClient({
        "health": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["mcp_health_server.py"],
            "cwd": HERE,
        }
    })
    tools = await client.get_tools()

    # AsyncSqliteSaver 是异步上下文管理器：整个服务生命周期内保持打开。
    # 之所以不用 MemorySaver，是因为那样一旦重启，所有暂停中的会话都会丢，
    # "人机协同"就变成了"人必须在进程活着的时候点确认"。
    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as saver:
        app.state.graph = hg.build_graph(tools, checkpointer=saver)
        print(f"✅ MCP 工具加载完成：{[t.name for t in tools]}")
        print(f"✅ 检查点落盘：{CHECKPOINT_DB}")
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


def remember_profile(user_id: str, profile_input: dict | None) -> None:
    """画像一解析出来就落长期记忆。

    与"是否接受计划"无关——画像本身是既成事实。早点存的好处是：
    用户在确认闸门点了"需要调整"、或者干脆关掉页面，画像也不会丢。
    """
    if not profile_input:
        return
    memory.save_profile(user_id, profile_input)
    weight = profile_input.get("weight_kg")
    if weight:
        try:
            memory.append_weight(user_id, float(weight))
        except (TypeError, ValueError):
            # 体重可能是"65公斤"这类字符串；画像仍要保存，体重记录跳过即可
            obs.log_event(
                {
                    "kind": "memory",
                    "name": "append_weight",
                    "ok": False,
                    "error": f"体重不是数值：{weight!r}",
                }
            )


async def stream_graph(graph, graph_input, *, thread_id: str, trace_id: str,
                       session_id: str, user_id: str, is_resume: bool = False):
    """把一次 LangGraph 调用（新建或恢复）转成 SSE 事件流。

    /api/chat 与 /api/chat/resume 共用这段逻辑——两份实现迟早会走偏。
    正常结束才写长期记忆：计划没定稿时不该覆盖上一版计划。
    """
    config = hg.run_config(thread_id)
    final_json = ""

    async for update in graph.astream(graph_input, config=config, stream_mode="updates"):
        # 中断帧：图在闸门处暂停，把"要人决定什么"交给前端
        if "__interrupt__" in update:
            payload = hg.extract_interrupt(update) or {}
            obs.log_event(
                {
                    "kind": "hitl",
                    "name": payload.get("type", "interrupt"),
                    "ok": True,
                    "question": payload.get("question", ""),
                }
            )
            yield sse(
                {
                    "type": "interrupt",
                    "trace_id": trace_id,
                    "thread_id": thread_id,
                    "interrupt": payload,
                }
            )
            return

        for node_name, node_output in update.items():
            emoji, name, key = NODES.get(node_name, ("🤖", node_name, None))
            if key is None or not isinstance(node_output, dict):
                continue
            content = node_output.get(key, "")
            if not content:
                continue
            if node_name == "profile_parser":
                remember_profile(user_id, node_output.get("profile_input"))
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

    # 走到这里说明图已经跑完。最终计划必须从**图状态**里取，不能只看这一段流：
    # 计划可能是在上一段流里产出的——plan_composer 跑完 → 确认闸门中断 →
    # 用户点「接受」→ 恢复流里不会再有任何内容帧。
    # 只看本段流的话，用户接受之后计划永远不会被写进长期记忆，
    # 下一轮「追问调整」就会拿着空的上一版计划去跑。
    snapshot = await graph.aget_state(config)
    final_json = (getattr(snapshot, "values", None) or {}).get("final_json") or final_json

    if final_json:
        memory.append_message(session_id, "assistant", final_json)
        memory.save_last_plan(user_id, final_json)
    obs.log_event({"kind": "response", "name": "chat", "ok": True, "is_resume": is_resume})
    yield "data: [DONE]\n\n"


def sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """SSE 流式对话接口：加载记忆 → 意图路由 → 跑图 → 保存记忆。"""
    graph = app.state.graph
    history = memory.get_history(req.session_id)
    last_plan = memory.get_last_plan(req.user_id) or ""
    profile_input = memory.get_profile(req.user_id) or {}
    user_context = build_user_context(req.user_id, profile_input)
    intent = hg.detect_intent(req.message, last_plan)

    # trace_id 在这里开启，之后所有节点 / 模型 / 工具事件都会自动带上它
    trace_id = obs.new_trace(req.session_id, req.user_id, intent)
    obs.count_request()
    obs.log_event({"kind": "intent", "name": "detect_intent", "ok": True, "value": intent})

    # 每次请求一个新的 thread_id：它既是检查点的键，也是前端恢复中断的凭据
    thread_id = f"{req.session_id or 'anon'}:{uuid.uuid4().hex[:8]}"
    memory.append_message(req.session_id, "user", req.message)

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

    ctx = {
        "thread_id": thread_id,
        "trace_id": trace_id,
        "session_id": req.session_id,
        "user_id": req.user_id,
    }

    async def event_stream():
        yield sse({"type": "trace", "trace_id": trace_id, "thread_id": thread_id})
        yield sse({"node": "intent", "name": "意图判断", "content": intent, "intent": intent})
        async for chunk in stream_graph(graph, initial, **ctx):
            yield chunk

    return sse_response(event_stream())


@app.post("/api/chat/resume")
async def resume(req: ResumeRequest):
    """恢复一个在闸门处暂停的图。

    前端收到中断帧后把用户的决定 POST 到这里，接着同一条 SSE 流往下跑；
    如果还有下一个闸门，会再次收到中断帧。
    """
    graph = app.state.graph

    # session_id / user_id 一律以图状态为准：检查点里就存着这两个字段。
    # 前端恢复请求只带 thread_id 也完全够用——若依赖前端回传，一旦漏传，
    # 最终计划就会被写到 "default" 用户名下，而且全程没有任何报错。
    snapshot = await graph.aget_state({"configurable": {"thread_id": req.thread_id}})
    values = getattr(snapshot, "values", None) or {}
    session_id = values.get("session_id") or req.session_id
    user_id = values.get("user_id") or req.user_id

    obs.count_request()
    trace_id = obs.new_trace(session_id, user_id, "resume")
    obs.log_event(
        {
            "kind": "hitl",
            "name": "human_decision",
            "ok": True,
            # thread_id 是把"恢复请求"和"原始请求"两条 trace 关联起来的连接键
            "thread_id": req.thread_id,
            "decision": req.decision,
            "feedback": req.feedback,
        }
    )

    ctx = {
        "thread_id": req.thread_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "user_id": user_id,
    }

    async def event_stream():
        yield sse({"type": "trace", "trace_id": trace_id, "thread_id": req.thread_id})
        # 前端的决定以结构化字典恢复：闸门节点会优先读 decision，
        # 并把 feedback 当作"调整意见"传给 adjust_planner
        resume_value = {"decision": req.decision, "feedback": req.feedback}
        async for chunk in stream_graph(graph, Command(resume=resume_value), is_resume=True, **ctx):
            yield chunk

    return sse_response(event_stream())


@app.post("/api/feedback")
async def feedback(req: FeedbackRequest):
    """记录用户是否接受最终结果（可观测性要回答的问题之一）。"""
    obs.record_feedback(req.trace_id, req.accepted, req.comment)
    return {"ok": True}


@app.get("/api/metrics")
async def metrics():
    """聚合指标：延迟分位、token 成本、工具成功率、失败发生在哪一步。"""
    return obs.snapshot()


@app.get("/api/traces")
async def traces(limit: int = 20):
    """最近若干次请求的摘要，用于定位"哪一次出了问题"。"""
    return obs.recent_traces(limit)


@app.get("/api/trace/{trace_id}")
async def trace_detail(trace_id: str):
    """单次请求的完整事件链：调了哪些节点、用了什么模型、每步耗时。"""
    events = obs.get_trace(trace_id)
    if not events:
        raise HTTPException(status_code=404, detail="trace 不存在或已滚出内存窗口")
    return {"trace_id": trace_id, "events": events}


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
