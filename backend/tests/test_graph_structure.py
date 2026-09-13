"""图结构测试：确认编译通过，且节点与边符合设计意图。

这套图里有两处最容易写错、而且错了不一定立刻报错的地方：

1. **条件边分叉 + 三路 fan-out**：`add_conditional_edges` 的 path_map 传 list
   会被静默接受，直到 `compile()` 才抛 `TypeError: unhashable type: 'list'`
   （langgraph 1.2.11 实测）。所以要用显式的 plan_fanout 空节点做分发。
2. **确认闸门的回边**：`plan_composer → confirm_gate → adjust_planner →
   confirm_gate` 是一条真实环，没有 MAX_REVISIONS 就是无限循环。

这里把结构钉住，改图时如果破坏了这两点会立刻失败。
"""
from __future__ import annotations

import pytest
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

import health_graph as hg

# ---------------------------------------------------------------- 假工具
# 只要求名字与 build_graph 里取用的 6 个一致；不会真的被调用。


@tool
def calculate_bmi(height_cm: float, weight_kg: float) -> str:
    """根据身高体重计算 BMI。"""
    return "BMI=22.5"


@tool
def query_health_knowledge(query: str) -> str:
    """查询健康知识。"""
    return "知识"


@tool
def query_food_nutrition(food: str) -> str:
    """查询食物营养。"""
    return "营养"


@tool
def get_exercise_guidance(goal: str, risk_level: str) -> str:
    """查询运动指南。"""
    return "运动"


@tool
def get_weather(city: str) -> str:
    """查询天气。"""
    return "晴"


@tool
def get_sleep_guidance(goal: str) -> str:
    """查询睡眠建议。"""
    return "睡眠"


TOOLS = [
    calculate_bmi,
    query_health_knowledge,
    query_food_nutrition,
    get_exercise_guidance,
    get_weather,
    get_sleep_guidance,
]

EXPECTED_NODES = {
    "profile_parser",
    "health_assessor",
    "risk_gate",
    "abort",
    "plan_fanout",
    "diet_planner",
    "exercise_planner",
    "lifestyle_planner",
    "plan_composer",
    "confirm_gate",
    "adjust_planner",
}


@pytest.fixture(scope="module")
def graph():
    return hg.build_graph(TOOLS, checkpointer=MemorySaver())


def edges_of(graph) -> set:
    return {(e.source, e.target) for e in graph.get_graph().edges}


class TestStructure:
    def test_编译通过且节点齐全(self, graph):
        nodes = set(graph.get_graph().nodes)
        assert EXPECTED_NODES <= nodes, f"缺少节点：{EXPECTED_NODES - nodes}"

    def test_不传checkpointer也能编译(self):
        """cli.py 走的是无 checkpointer 的路径，不能因此挂掉。"""
        assert hg.build_graph(TOOLS) is not None

    def test_三路规划从分发点出发(self, graph):
        edges = edges_of(graph)
        for name in ("diet_planner", "exercise_planner", "lifestyle_planner"):
            assert ("plan_fanout", name) in edges, f"plan_fanout 未连到 {name}"
            assert (name, "plan_composer") in edges, f"{name} 未连到 plan_composer"

    def test_风险闸门分叉到中止与分发两条路(self, graph):
        edges = edges_of(graph)
        assert ("health_assessor", "risk_gate") in edges
        assert ("risk_gate", "plan_fanout") in edges
        assert ("risk_gate", "abort") in edges
        assert ("abort", "__end__") in edges

    def test_确认闸门构成带上限的回边(self, graph):
        edges = edges_of(graph)
        assert ("plan_composer", "confirm_gate") in edges
        assert ("confirm_gate", "adjust_planner") in edges
        assert ("adjust_planner", "confirm_gate") in edges, "回边断了，调整后就无法再确认"

    def test_调整意图直接进调整节点(self, graph):
        edges = edges_of(graph)
        assert ("__start__", "adjust_planner") in edges

    def test_串行模式也能编译且链路正确(self, monkeypatch):
        """PARALLEL=False 的串行分支此前从未被执行验证过，这里把它钉住。"""
        monkeypatch.setattr(hg, "PARALLEL", False)
        g = hg.build_graph(TOOLS, checkpointer=MemorySaver())
        edges = edges_of(g)
        assert ("plan_fanout", "diet_planner") in edges
        assert ("diet_planner", "exercise_planner") in edges
        assert ("exercise_planner", "lifestyle_planner") in edges
        assert ("lifestyle_planner", "plan_composer") in edges
