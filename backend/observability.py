"""可观测性：让每次请求都能回答"调了什么、花了多少、失败在哪一步"。

对应《什么样的 Agent 项目才算好项目》第五节「要有可观测性和评测体系」——
企业环境里 Agent 必须能被监控、追踪和评估，至少要能回答八个问题：

  1. 每次请求调用了哪些 Agent？   -> events 里 kind="node" 的记录
  2. 每次调用用了哪个模型？       -> events 里 kind="llm" 的 model 字段
  3. 工具调用是否成功？           -> events 里 kind="tool" 的 ok 字段
  4. 失败发生在哪一步？           -> ok=false 的 name + error 字段
  5. 平均响应时间是多少？         -> snapshot()["latency_ms"]
  6. Token 成本是多少？           -> snapshot()["cost"]
  7. 用户是否接受最终结果？       -> record_feedback() 之后看 accepted_rate
  8. 输出结果准确率如何？         -> 离线由 eval.py 给出，见 README「评测」一节

设计取舍
--------
* 用 LangChain 的 CallbackHandler 采集 LLM / 工具调用，而不是在每个调用点手写埋点：
  create_agent 内部的工具循环也能被自动覆盖，对业务代码的侵入最小。
* trace_id 用 contextvars 传递，asyncio 子任务与 to_thread 都会继承上下文。
* 事件落盘为 JSON Lines（logs/trace.jsonl）：一行一个事件，
  grep / jq / pandas 都能直接消费，不需要额外的可观测平台就能查问题。
* 聚合指标放在进程内存（够单机演示）；上生产应换 Prometheus 或 Langfuse，
  但事件格式保持不变，替换成本很低。
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

try:
    from langgraph.errors import GraphBubbleUp
except ImportError:  # pragma: no cover - langgraph 是运行依赖，这里只是让本模块可独立使用

    class GraphBubbleUp(Exception):  # type: ignore[no-redef]
        """兜底：没有 langgraph 时不会把任何异常误判成「暂停」。"""

# ---------------------------------------------------------------- 配置

_LOG_DIR = Path(os.getenv("TRACE_DIR", Path(__file__).resolve().parent.parent / "logs"))
_LOG_FILE = _LOG_DIR / "trace.jsonl"

# 单价（元 / 百万 token）。默认值按 DeepSeek 官方价目表估算，
# 但价格会变，所以一律允许用环境变量覆盖 —— 成本数字必须可追溯来源。
_PRICE_IN = float(os.getenv("PRICE_INPUT_PER_M", "2.0"))
_PRICE_OUT = float(os.getenv("PRICE_OUTPUT_PER_M", "8.0"))

# 单条事件里字符串字段的截断长度，避免把整份计划塞进日志
_MAX_TEXT = int(os.getenv("TRACE_MAX_TEXT", "400"))

# ---------------------------------------------------------------- Trace 上下文

_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
# 兜底：LangGraph 若在未继承上下文的线程里执行同步节点，contextvar 会取不到值
_FALLBACK_TRACE_ID = ""


def new_trace(session_id: str = "", user_id: str = "default", intent: str = "") -> str:
    """开启一条新的追踪链，返回 trace_id。"""
    global _FALLBACK_TRACE_ID
    trace_id = uuid.uuid4().hex[:16]
    _TRACE_ID.set(trace_id)
    _FALLBACK_TRACE_ID = trace_id
    log_event(
        {
            "kind": "request",
            "name": "chat",
            "session_id": session_id,
            "user_id": user_id,
            "intent": intent,
        }
    )
    return trace_id


def current_trace_id() -> str:
    """取当前 trace_id；contextvar 拿不到时退回最近一次开启的 trace。"""
    return _TRACE_ID.get() or _FALLBACK_TRACE_ID


def reset_state() -> None:
    """清空聚合指标与当前 trace（供测试使用）。"""
    global _FALLBACK_TRACE_ID
    _FALLBACK_TRACE_ID = ""
    _TRACE_ID.set("")
    with _LOCK:
        _METRICS.clear()
        _EVENTS.clear()
        _METRICS.update(_empty_metrics())


# ---------------------------------------------------------------- 事件写入

_LOCK = threading.Lock()
# 单独一把锁给文件写入：不把磁盘 I/O 的延迟耦合到内存指标的更新上
_FILE_LOCK = threading.Lock()
_EVENTS: dict[str, list] = {}


def _empty_metrics() -> dict:
    return {
        "requests": 0,
        "accepted": 0,
        "rejected": 0,
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "tool_calls": 0,
        "tool_failures": 0,
        "node_failures": 0,
        "nodes": {},        # 节点名 -> {"count": n, "total_ms": x, "failures": k}
        "llm_by_model": {},  # 模型名 -> 调用次数
        "failures_by_step": {},  # 失败点 -> 次数（回答"失败发生在哪一步"）
    }


_METRICS: dict = _empty_metrics()


def _clip(value):
    """把任意值转成可 JSON 序列化、且长度受控的形式。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + f"…(+{len(value) - _MAX_TEXT})"
    if isinstance(value, (list, tuple)):
        return [_clip(v) for v in list(value)[:20]]
    if isinstance(value, dict):
        return {str(k): _clip(v) for k, v in list(value.items())[:20]}
    return _clip(str(value))


def log_event(event: dict) -> dict:
    """写一条结构化事件：进内存（供 /api/trace 查询）+ 追加到 JSONL 文件。

    **文件写入必须持锁**。LangGraph 会把同步节点丢进线程池执行，
    多个线程同时 `open(..., "a")` 再 write，两次写入的字节会互相穿插，
    产出既不是合法 UTF-8、也不是合法 JSON 的行。
    实测踩过：`logs/trace.jsonl` 中间出现 `'}"}'` 这样的碎片、
    出现半个汉字（0xad 起头），整份文件无法被 json / pandas 解析。

    用单独的 `_FILE_LOCK`，不把磁盘 I/O 的延迟耦合到内存指标的更新上。
    """
    record = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "trace_id": current_trace_id(),
        **{k: _clip(v) for k, v in event.items()},
    }
    tid = record["trace_id"]
    with _LOCK:
        _EVENTS.setdefault(tid, []).append(record)
        # 只保留最近 200 条 trace，防止长时间运行把内存吃满
        if len(_EVENTS) > 200:
            for stale in list(_EVENTS)[:-200]:
                _EVENTS.pop(stale, None)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _FILE_LOCK:
            with _LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError:
        # 日志盘写不进去不能影响主流程；可观测性失败必须是 fail-open
        pass
    return record


# ---------------------------------------------------------------- 计时 span

@contextmanager
def span(name: str, kind: str = "step", **fields):
    """给一段代码计时并记录成功/失败，异常照常向上抛。"""
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - started) * 1000
        log_event(
            {
                "kind": kind,
                "name": name,
                "ok": False,
                "duration_ms": round(elapsed, 1),
                "error": f"{type(exc).__name__}: {exc}",
                **fields,
            }
        )
        with _LOCK:
            _METRICS["failures_by_step"][name] = _METRICS["failures_by_step"].get(name, 0) + 1
        raise
    else:
        elapsed = (time.perf_counter() - started) * 1000
        log_event(
            {"kind": kind, "name": name, "ok": True, "duration_ms": round(elapsed, 1), **fields}
        )


def timed_node(name: str, fn=None):
    """包装一个 LangGraph 节点：记录耗时、成功/失败与产出字段名。

    并行 fan-out 的三个节点各自计时，因此这里拿到的是准确的节点级耗时，
    而不是用"两次 SSE 事件的时间差"去猜。

    两种用法都支持：
        g.add_node("diet_planner", timed_node("diet_planner", diet_planner_node))

        @timed_node("diet_planner")
        def diet_planner_node(state): ...
    """
    if fn is None:
        return lambda f: timed_node(name, f)
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(state):
            started = time.perf_counter()
            try:
                result = await fn(state)
            except Exception as exc:  # noqa: BLE001
                _node_exception(name, started, exc)
                raise
            _record_node(
                name, started, ok=True,
                produced=sorted(result) if isinstance(result, dict) else None,
            )
            return result

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(state):
        started = time.perf_counter()
        try:
            result = fn(state)
        except Exception as exc:  # noqa: BLE001
            _node_exception(name, started, exc)
            raise
        _record_node(
            name, started, ok=True,
            produced=sorted(result) if isinstance(result, dict) else None,
        )
        return result

    return wrapper


def _node_exception(name: str, started: float, exc: Exception) -> None:
    """区分「节点真的失败」与「节点被 interrupt 暂停」。

    `interrupt()` 靠抛 `GraphBubbleUp` 来暂停整张图 —— 那是控制流信号，不是错误
    （langgraph 源码注释：Never raised directly, or surfaced to the user）。
    如果记成失败，**每一次人机协同都会在 failures_by_step 里留一条假记录**，
    "失败发生在哪一步"这个问题就被污染了。
    实测：一次正常的计划确认中断，会让 confirm_gate 出现在 trace 的 failed 列表里。
    """
    if isinstance(exc, GraphBubbleUp):
        _record_node(name, started, ok=True, paused=True)
    else:
        _record_node(name, started, ok=False, error=f"{type(exc).__name__}: {exc}")


def _record_node(name: str, started: float, ok: bool, **extra) -> None:
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    log_event({"kind": "node", "name": name, "ok": ok, "duration_ms": elapsed, **extra})
    with _LOCK:
        bucket = _METRICS["nodes"].setdefault(name, {"count": 0, "total_ms": 0.0, "failures": 0})
        bucket["count"] += 1
        bucket["total_ms"] += elapsed
        if not ok:
            bucket["failures"] += 1
            _METRICS["node_failures"] += 1


# ---------------------------------------------------------------- LLM / 工具回调

def _extract_usage(response) -> dict:
    """兼容两种 usage 位置：llm_output.token_usage 与 message.usage_metadata。"""
    usage = {}
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if not usage:
        try:
            msg = response.generations[0][0].message
            usage = getattr(msg, "usage_metadata", None) or {}
        except (AttributeError, IndexError, TypeError):
            usage = {}
    return {
        "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
        "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
    }


class TraceCallbackHandler(BaseCallbackHandler):
    """把 LangChain 的 LLM / 工具回调翻译成结构化事件。

    挂在 make_llm 返回的模型上，create_agent 内部的工具循环因此自动被覆盖，
    不需要为每个工具单独写埋点。
    """

    def __init__(self) -> None:
        super().__init__()
        self._llm_started: dict = {}
        self._tool_started: dict = {}

    # ---- LLM ----
    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):  # noqa: D102
        self._llm_started[str(run_id)] = time.perf_counter()
        log_event(
            {
                "kind": "llm",
                "name": "llm_call",
                "phase": "start",
                "model": (serialized or {}).get("kwargs", {}).get("model")
                or (serialized or {}).get("name", "unknown"),
                "prompt_chars": sum(len(p) for p in (prompts or [])),
            }
        )

    def on_llm_end(self, response, *, run_id=None, **kwargs):  # noqa: D102
        started = self._llm_started.pop(str(run_id), time.perf_counter())
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        usage = _extract_usage(response)
        model = ((getattr(response, "llm_output", None) or {}).get("model_name")) or "unknown"
        # 单次成本也写进事件：否则事后只能看到总账，无法回答
        # "哪一次请求最贵"——而这恰恰是优化时最想知道的事
        cost = round(
            usage["prompt_tokens"] / 1_000_000 * _PRICE_IN
            + usage["completion_tokens"] / 1_000_000 * _PRICE_OUT,
            6,
        )
        log_event(
            {
                "kind": "llm",
                "name": "llm_call",
                "phase": "end",
                "ok": True,
                "model": model,
                "duration_ms": elapsed,
                "cost_cny": cost,
                **usage,
            }
        )
        with _LOCK:
            _METRICS["llm_calls"] += 1
            _METRICS["prompt_tokens"] += usage["prompt_tokens"]
            _METRICS["completion_tokens"] += usage["completion_tokens"]
            _METRICS["llm_by_model"][model] = _METRICS["llm_by_model"].get(model, 0) + 1

    def on_llm_error(self, error, *, run_id=None, **kwargs):  # noqa: D102
        started = self._llm_started.pop(str(run_id), time.perf_counter())
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        log_event(
            {
                "kind": "llm",
                "name": "llm_call",
                "phase": "error",
                "ok": False,
                "duration_ms": elapsed,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        with _LOCK:
            _METRICS["failures_by_step"]["llm_call"] = (
                _METRICS["failures_by_step"].get("llm_call", 0) + 1
            )

    # ---- 工具（含 create_agent 内部循环里的调用）----
    def on_tool_start(self, serialized, input_str, *, run_id=None, **kwargs):  # noqa: D102
        self._tool_started[str(run_id)] = time.perf_counter()
        log_event(
            {
                "kind": "tool",
                "name": (serialized or {}).get("name", "unknown_tool"),
                "phase": "start",
                "input": input_str,
            }
        )

    def on_tool_end(self, output, *, run_id=None, **kwargs):  # noqa: D102
        started = self._tool_started.pop(str(run_id), time.perf_counter())
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        log_event(
            {
                "kind": "tool",
                "name": "tool_call",
                "phase": "end",
                "ok": True,
                "duration_ms": elapsed,
                "output": output,
            }
        )
        with _LOCK:
            _METRICS["tool_calls"] += 1

    def on_tool_error(self, error, *, run_id=None, **kwargs):  # noqa: D102
        started = self._tool_started.pop(str(run_id), time.perf_counter())
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        log_event(
            {
                "kind": "tool",
                "name": "tool_call",
                "phase": "error",
                "ok": False,
                "duration_ms": elapsed,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        with _LOCK:
            _METRICS["tool_calls"] += 1
            _METRICS["tool_failures"] += 1
            _METRICS["failures_by_step"]["tool_call"] = (
                _METRICS["failures_by_step"].get("tool_call", 0) + 1
            )


# ---------------------------------------------------------------- 用户反馈

def record_feedback(trace_id: str, accepted: bool, comment: str = "") -> None:
    """记录"用户是否接受最终结果"（对应第 7 问）。"""
    log_event(
        {
            "kind": "feedback",
            "name": "user_feedback",
            "ok": True,
            "accepted": bool(accepted),
            "comment": comment,
            "target_trace_id": trace_id,
        }
    )
    with _LOCK:
        _METRICS["accepted" if accepted else "rejected"] += 1


def count_request() -> None:
    """一次用户请求开始处理时调用。"""
    with _LOCK:
        _METRICS["requests"] += 1


# ---------------------------------------------------------------- 聚合查询

def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 1)


def snapshot() -> dict:
    """把聚合指标整理成"回答那八个问题"的形状。"""
    with _LOCK:
        m = json.loads(json.dumps(_METRICS))  # 深拷贝，避免调用方改到内部状态

    llm_latencies = [
        e["duration_ms"] for e in _all_events() if e.get("kind") == "llm" and e.get("phase") == "end"
    ]
    node_latencies = [
        e["duration_ms"]
        for e in _all_events()
        if e.get("kind") == "node" and e.get("ok") and e.get("duration_ms") is not None
    ]
    tool_latencies = [
        e["duration_ms"] for e in _all_events() if e.get("kind") == "tool" and e.get("phase") == "end"
    ]

    cost_in = m["prompt_tokens"] / 1_000_000 * _PRICE_IN
    cost_out = m["completion_tokens"] / 1_000_000 * _PRICE_OUT
    feedback_total = m["accepted"] + m["rejected"]
    nodes = {
        name: {
            "calls": b["count"],
            "failures": b["failures"],
            "avg_ms": round(b["total_ms"] / b["count"], 1) if b["count"] else 0.0,
        }
        for name, b in m["nodes"].items()
    }
    return {
        "totals": {
            "requests": m["requests"],
            "llm_calls": m["llm_calls"],
            "tool_calls": m["tool_calls"],
            "tool_success_rate": (
                round(1 - m["tool_failures"] / m["tool_calls"], 4) if m["tool_calls"] else None
            ),
            "node_failures": m["node_failures"],
        },
        "latency_ms": {
            "llm": {"p50": _percentile(llm_latencies, 0.5), "p95": _percentile(llm_latencies, 0.95)},
            "node": {"p50": _percentile(node_latencies, 0.5), "p95": _percentile(node_latencies, 0.95)},
            "tool": {"p50": _percentile(tool_latencies, 0.5), "p95": _percentile(tool_latencies, 0.95)},
        },
        "cost": {
            "prompt_tokens": m["prompt_tokens"],
            "completion_tokens": m["completion_tokens"],
            "total_tokens": m["prompt_tokens"] + m["completion_tokens"],
            "cost_cny": round(cost_in + cost_out, 6),
            "price_per_m_input": _PRICE_IN,
            "price_per_m_output": _PRICE_OUT,
            "note": "按 .env 里的单价估算，单价可覆盖",
        },
        "nodes": nodes,
        "llm_by_model": m["llm_by_model"],
        "failures_by_step": m["failures_by_step"],
        "feedback": {
            "accepted": m["accepted"],
            "rejected": m["rejected"],
            "accepted_rate": round(m["accepted"] / feedback_total, 4) if feedback_total else None,
        },
        # 把"这些数字的适用范围"直接写进响应里：多 worker 部署时每个 worker
        # 各算各的，不写清楚会被当成全局指标读。
        "scope": "单进程内存聚合（本次 uvicorn 进程启动至今）；多 worker 部署需外部聚合",
    }


def _all_events() -> list:
    with _LOCK:
        return [e for events in _EVENTS.values() for e in events]


def get_trace(trace_id: str) -> list:
    """按 trace_id 取回完整事件链（回答"这次请求到底发生了什么"）。"""
    with _LOCK:
        return list(_EVENTS.get(trace_id, []))


def _wall_ms(events: list) -> float:
    """真实墙钟耗时：首末事件的 ts 之差。

    不能用 `sum(duration_ms)` 代替 —— 节点耗时**包含**其内部的 LLM 与工具调用，
    并行 fan-out 的三个节点又是同时跑的，累加会严重高估。
    实测：一次实际约 100 秒的请求，累加出来是 278 秒。
    """
    if len(events) < 2:
        return 0.0
    try:
        start = datetime.fromisoformat(events[0]["ts"])
        end = datetime.fromisoformat(events[-1]["ts"])
        return round((end - start).total_seconds() * 1000, 1)
    except (KeyError, ValueError):
        return 0.0


def recent_traces(limit: int = 20) -> list:
    """最近 N 条 trace 的摘要，供 /api/traces 列表页使用。"""
    with _LOCK:
        items = list(_EVENTS.items())[-limit:]
    out = []
    for tid, events in items:
        req = next((e for e in events if e.get("kind") == "request"), {})
        failed = [e for e in events if e.get("ok") is False]
        paused = [e for e in events if e.get("paused")]
        out.append(
            {
                "trace_id": tid,
                "ts": events[0]["ts"] if events else "",
                "session_id": req.get("session_id", ""),
                "user_id": req.get("user_id", ""),
                "intent": req.get("intent", ""),
                "events": len(events),
                "failed_steps": [e.get("name") for e in failed],
                "paused_steps": [e.get("name") for e in paused],
                # 两个数都给，且名字说清各自是什么：墙钟是"用户等了多久"，
                # 跨度累加是"总共花了多少计算时间"（并行时会大于墙钟）
                "wall_ms": _wall_ms(events),
                "span_sum_ms": round(sum(e.get("duration_ms", 0) for e in events), 1),
            }
        )
    return out
