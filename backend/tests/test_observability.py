"""observability.py 单元测试。

可观测性代码有个特点：它坏了不会报错，只会悄悄少记数据——
等到你真需要查"昨天那次失败发生在哪一步"时才发现日志是空的。
所以这里重点验证三件事：

1. 事件确实被记下来了，且字段齐全（能回答作者列出的八个问题）；
2. trace_id 在异步调用链里传得下去（contextvars 在 asyncio 任务中生效）；
3. 聚合指标算得对（token 累加、成本换算、失败归因、百分位）。

最后一个用例专门验证"可观测性自身失败必须 fail-open"：
日志写不进去时，主流程不能跟着挂。
"""
from __future__ import annotations

import asyncio
import json

import pytest

import observability as obs


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """每个用例独立：清空聚合指标 + 把日志重定向到临时目录。"""
    monkeypatch.setattr(obs, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(obs, "_LOG_FILE", tmp_path / "trace.jsonl")
    obs.reset_state()
    yield
    obs.reset_state()


def read_jsonl(tmp_path):
    path = tmp_path / "trace.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- trace 上下文
class TestTraceContext:
    def test_新trace产生唯一id(self):
        a = obs.new_trace("s1", "u1", "generate")
        b = obs.new_trace("s2", "u2", "adjust")
        assert a and b and a != b
        assert len(a) == 16

    def test_current_trace_id跟随最近一次开启(self):
        tid = obs.new_trace("s1")
        assert obs.current_trace_id() == tid

    def test_事件自动带上当前trace_id(self):
        tid = obs.new_trace("s1")
        event = obs.log_event({"kind": "custom", "name": "x"})
        assert event["trace_id"] == tid

    def test_异步任务里仍能取到trace_id(self, tmp_path):
        """LangGraph 会在 asyncio 子任务里跑异步节点，上下文必须传得下去。"""

        async def child():
            return obs.current_trace_id()

        async def main():
            tid = obs.new_trace("s1")
            seen = await asyncio.gather(child(), child())
            return tid, seen

        tid, seen = asyncio.run(main())
        assert seen == [tid, tid]

    def test_线程池里仍能取到trace_id(self):
        """同步节点可能被丢进线程池执行。"""

        async def main():
            tid = obs.new_trace("s1")
            seen = await asyncio.to_thread(obs.current_trace_id)
            return tid, seen

        tid, seen = asyncio.run(main())
        assert seen == tid


# ---------------------------------------------------------------- 落盘
class TestEventPersistence:
    def test_事件写入jsonl文件(self, tmp_path):
        obs.new_trace("s1")
        obs.log_event({"kind": "node", "name": "plan_composer", "ok": True, "duration_ms": 12.5})
        rows = read_jsonl(tmp_path)
        assert len(rows) == 2  # request + node
        assert rows[1]["name"] == "plan_composer"
        assert rows[1]["duration_ms"] == 12.5

    def test_每行都是合法json且含时间戳(self, tmp_path):
        obs.new_trace("s1")
        obs.log_event({"kind": "x", "name": "y"})
        for row in read_jsonl(tmp_path):
            assert "ts" in row and "trace_id" in row

    def test_超长文本被截断(self, tmp_path):
        obs.new_trace("s1")
        obs.log_event({"kind": "llm", "name": "x", "output": "啊" * 5000})
        row = read_jsonl(tmp_path)[-1]
        assert len(row["output"]) < 5000
        assert "…(+" in row["output"]

    def test_不可序列化对象被转成字符串(self, tmp_path):
        obs.new_trace("s1")
        obs.log_event({"kind": "x", "name": "y", "obj": object()})
        assert isinstance(read_jsonl(tmp_path)[-1]["obj"], str)

    def test_日志目录不可写时不抛异常(self, monkeypatch, tmp_path):
        """可观测性必须 fail-open：日志坏掉不能连带把业务请求打死。

        把"日志目录"指向一个已经存在的普通文件，mkdir 必然抛 OSError
        （Windows 是 FileExistsError，POSIX 是 NotADirectoryError，都是 OSError 子类）。
        """
        obs.new_trace("s1")
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(obs, "_LOG_DIR", blocker)
        monkeypatch.setattr(obs, "_LOG_FILE", blocker / "trace.jsonl")
        event = obs.log_event({"kind": "x", "name": "y"})  # 不应抛
        assert event["kind"] == "x"


# ---------------------------------------------------------------- span
class TestSpan:
    def test_成功时记录耗时与ok(self, tmp_path):
        obs.new_trace("s1")
        with obs.span("my_step", kind="step"):
            pass
        row = read_jsonl(tmp_path)[-1]
        assert row["name"] == "my_step"
        assert row["ok"] is True
        assert row["duration_ms"] >= 0

    def test_失败时记录异常并继续向上抛(self, tmp_path):
        obs.new_trace("s1")
        with pytest.raises(ValueError, match="炸了"):
            with obs.span("bad_step"):
                raise ValueError("炸了")
        row = read_jsonl(tmp_path)[-1]
        assert row["ok"] is False
        assert "ValueError: 炸了" in row["error"]

    def test_失败点进入failures_by_step(self):
        obs.new_trace("s1")
        with pytest.raises(RuntimeError):
            with obs.span("llm_call"):
                raise RuntimeError("超时")
        assert obs.snapshot()["failures_by_step"] == {"llm_call": 1}


# ---------------------------------------------------------------- 节点包装
class TestTimedNode:
    def test_同步节点被计时并记录产出字段(self, tmp_path):
        obs.new_trace("s1")

        @obs.timed_node("diet_planner")
        def node(state):
            return {"diet_plan": "x"}

        assert node({}) == {"diet_plan": "x"}
        row = read_jsonl(tmp_path)[-1]
        assert row["kind"] == "node"
        assert row["name"] == "diet_planner"
        assert row["produced"] == ["diet_plan"]

    def test_异步节点被计时(self, tmp_path):
        obs.new_trace("s1")

        @obs.timed_node("health_assessor")
        async def node(state):
            await asyncio.sleep(0)
            return {"assessment": "ok"}

        assert asyncio.run(node({})) == {"assessment": "ok"}
        assert read_jsonl(tmp_path)[-1]["name"] == "health_assessor"

    def test_节点抛异常时记录失败并向上抛(self, tmp_path):
        obs.new_trace("s1")

        @obs.timed_node("plan_composer")
        def node(state):
            raise KeyError("缺字段")

        with pytest.raises(KeyError):
            node({})
        row = read_jsonl(tmp_path)[-1]
        assert row["ok"] is False
        assert "KeyError" in row["error"]

    def test_函数名被保留(self):
        @obs.timed_node("x")
        def my_node(state):
            """文档。"""
            return {}

        assert my_node.__name__ == "my_node"

    def test_注册式用法同样有效(self):
        """build_graph 里用的是显式注册写法，不是装饰器。"""

        def raw(state):
            return {"final_json": "{}"}

        wrapped = obs.timed_node("plan_composer", raw)
        assert wrapped({}) == {"final_json": "{}"}
        assert obs.snapshot()["nodes"]["plan_composer"]["calls"] == 1


# ---------------------------------------------------------------- LLM 回调
def llm_response(prompt_tokens=100, completion_tokens=50, model="deepseek-v4-flash"):
    class _Msg:
        usage_metadata = {"input_tokens": prompt_tokens, "output_tokens": completion_tokens}

    class _Gen:
        message = _Msg()

    class _Resp:
        llm_output = {
            "model_name": model,
            "token_usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        generations = [[_Gen()]]

    return _Resp()


class TestLLMCallback:
    def test_记录模型名与token数(self, tmp_path):
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()
        h.on_llm_start({"kwargs": {"model": "deepseek-v4-flash"}}, ["提示词"], run_id="r1")
        h.on_llm_end(llm_response(), run_id="r1")

        rows = read_jsonl(tmp_path)
        end = [r for r in rows if r.get("phase") == "end"][0]
        assert end["model"] == "deepseek-v4-flash"
        assert end["prompt_tokens"] == 100
        assert end["completion_tokens"] == 50
        assert end["ok"] is True

    def test_累计到聚合指标(self):
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()
        for i in range(3):
            h.on_llm_start({}, ["p"], run_id=f"r{i}")
            h.on_llm_end(llm_response(), run_id=f"r{i}")
        snap = obs.snapshot()
        assert snap["totals"]["llm_calls"] == 3
        assert snap["cost"]["prompt_tokens"] == 300
        assert snap["cost"]["completion_tokens"] == 150

    def test_llm报错被记录(self, tmp_path):
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()
        h.on_llm_start({}, ["p"], run_id="r1")
        h.on_llm_error(TimeoutError("连接超时"), run_id="r1")
        row = read_jsonl(tmp_path)[-1]
        assert row["ok"] is False
        assert "TimeoutError" in row["error"]
        assert obs.snapshot()["failures_by_step"]["llm_call"] == 1

    def test_usage缺失时不报错(self):
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()

        class Empty:
            llm_output = {}
            generations = []

        h.on_llm_start({}, ["p"], run_id="r1")
        h.on_llm_end(Empty(), run_id="r1")
        assert obs.snapshot()["cost"]["total_tokens"] == 0


class TestToolCallback:
    def test_成功的工具调用被计入成功率(self):
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()
        h.on_tool_start({"name": "calculate_bmi"}, "170,65", run_id="t1")
        h.on_tool_end("BMI=22.5", run_id="t1")
        snap = obs.snapshot()
        assert snap["totals"]["tool_calls"] == 1
        assert snap["totals"]["tool_success_rate"] == 1.0

    def test_工具失败进入失败归因(self, tmp_path):
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()
        h.on_tool_start({"name": "get_weather"}, "北京", run_id="t1")
        h.on_tool_error(RuntimeError("MCP 子进程挂了"), run_id="t1")
        row = read_jsonl(tmp_path)[-1]
        assert row["ok"] is False
        assert row["phase"] == "error"
        snap = obs.snapshot()
        assert snap["totals"]["tool_success_rate"] == 0.0
        assert snap["failures_by_step"]["tool_call"] == 1

    def test_混合成败时成功率正确(self):
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()
        for i in range(3):
            h.on_tool_start({"name": "t"}, "x", run_id=f"ok{i}")
            h.on_tool_end("y", run_id=f"ok{i}")
        h.on_tool_start({"name": "t"}, "x", run_id="bad")
        h.on_tool_error(RuntimeError("x"), run_id="bad")
        assert obs.snapshot()["totals"]["tool_success_rate"] == 0.75


# ---------------------------------------------------------------- 聚合
class TestSnapshot:
    def test_没有数据时不报错且成功率为None(self):
        snap = obs.snapshot()
        assert snap["totals"]["requests"] == 0
        assert snap["totals"]["tool_success_rate"] is None
        assert snap["feedback"]["accepted_rate"] is None

    def test_请求计数(self):
        for _ in range(4):
            obs.count_request()
        assert obs.snapshot()["totals"]["requests"] == 4

    def test_成本按单价换算(self, monkeypatch):
        monkeypatch.setattr(obs, "_PRICE_IN", 2.0)
        monkeypatch.setattr(obs, "_PRICE_OUT", 8.0)
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()
        h.on_llm_start({}, ["p"], run_id="r")
        h.on_llm_end(llm_response(1_000_000, 500_000), run_id="r")
        cost = obs.snapshot()["cost"]
        assert cost["cost_cny"] == pytest.approx(2.0 + 4.0, abs=0.01)

    def test_单价随配置变化(self, monkeypatch):
        monkeypatch.setattr(obs, "_PRICE_IN", 1.0)
        monkeypatch.setattr(obs, "_PRICE_OUT", 2.0)
        obs.new_trace("s1")
        h = obs.TraceCallbackHandler()
        h.on_llm_start({}, ["p"], run_id="r")
        h.on_llm_end(llm_response(1_000_000, 0), run_id="r")
        assert obs.snapshot()["cost"]["cost_cny"] == pytest.approx(1.0, abs=0.01)

    def test_节点统计包含调用次数与均值(self):
        obs.new_trace("s1")

        def node_fn(state):
            return {}

        timed = obs.timed_node("diet_planner", node_fn)
        for _ in range(3):
            timed({})
        node = obs.snapshot()["nodes"]["diet_planner"]
        assert node["calls"] == 3
        assert node["avg_ms"] >= 0
        assert node["failures"] == 0

    def test_百分位计算(self):
        assert obs._percentile([], 0.5) == 0.0
        assert obs._percentile([10.0], 0.95) == 10.0
        assert obs._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0

    def test_快照是深拷贝_改动不影响内部状态(self):
        obs.new_trace("s1")
        snap = obs.snapshot()
        snap["totals"]["requests"] = 999
        assert obs.snapshot()["totals"]["requests"] == 0

    def test_snapshot可json序列化(self):
        obs.new_trace("s1")
        json.dumps(obs.snapshot(), ensure_ascii=False)


class TestFeedback:
    def test_记录接受与拒绝(self):
        obs.new_trace("s1")
        obs.record_feedback("abc", True)
        obs.record_feedback("def", False)
        obs.record_feedback("ghi", True)
        fb = obs.snapshot()["feedback"]
        assert fb["accepted"] == 2
        assert fb["rejected"] == 1
        assert fb["accepted_rate"] == pytest.approx(0.6667, abs=0.001)


class TestTraceQuery:
    def test_按trace_id取回事件链(self):
        tid = obs.new_trace("s1", "u1", "generate")
        obs.log_event({"kind": "node", "name": "a"})
        obs.log_event({"kind": "node", "name": "b"})
        names = [e["name"] for e in obs.get_trace(tid)]
        assert names == ["chat", "a", "b"]

    def test_取不存在的trace返回空列表(self):
        assert obs.get_trace("nope") == []

    def test_recent_traces给出摘要与失败步骤(self):
        obs.new_trace("s1", "u1", "generate")
        obs.log_event({"kind": "node", "name": "a", "ok": True})
        obs.log_event({"kind": "llm", "name": "llm_call", "ok": False, "error": "x"})
        summary = obs.recent_traces()[0]
        assert summary["session_id"] == "s1"
        assert summary["intent"] == "generate"
        assert summary["failed_steps"] == ["llm_call"]

    def test_事件数量上限防止内存无限增长(self):
        for i in range(260):
            obs.new_trace(f"s{i}")
        assert len(obs._EVENTS) <= 200
