"""人机协同闸门测试（Human-in-the-loop）。

闸门是「要不要停下来问人」的决策，判错的代价不对称：
在需要就医的场景里，误判成「继续」远重于误判成「中止」。
所以这里重点验证 fail-closed 方向。

不依赖 LangGraph 运行时：直接 monkeypatch `health_graph.interrupt`，
把「用户点了什么」注入进去，单独验证决策逻辑与给前端的中断载荷。
"""
from __future__ import annotations

import json

import pytest

import health_graph as hg
from safety import DISCLAIMER
from state import MAX_REVISIONS


@pytest.fixture(autouse=True)
def silence_show(monkeypatch):
    """节点里的 show() 会往 stdout 打整份计划，测试里静音。"""
    monkeypatch.setattr(hg, "show", lambda *a, **k: None)


@pytest.fixture
def fake_interrupt(monkeypatch):
    """把 interrupt() 换成「返回预设值」，并记录它每次收到的 payload。"""
    calls = []

    def _fake(payload):
        calls.append(payload)
        return _fake.value

    _fake.value = "continue"
    _fake.calls = calls
    monkeypatch.setattr(hg, "interrupt", _fake)
    return _fake


# ---------------------------------------------------------------- 风险闸门
class TestRiskGate:
    def test_低风险直接放行且不打断(self, fake_interrupt):
        out = hg.risk_gate_node({"need_medical": False})
        assert out == {"risk_ack": True, "aborted": False}
        assert fake_interrupt.calls == []  # 没有停下来问人

    @pytest.mark.parametrize(
        "answer",
        ["continue", "继续", "我已了解，继续生成", True, {"decision": "continue"}, {"value": "continue"}],
    )
    def test_用户确认继续(self, fake_interrupt, answer):
        fake_interrupt.value = answer
        out = hg.risk_gate_node({"need_medical": True, "risk_level": "high"})
        assert out == {"risk_ack": True, "aborted": False}

    @pytest.mark.parametrize(
        "answer", ["abort", "取消", "先不生成", "我要先去看医生", False, {"decision": "abort"}]
    )
    def test_用户选择中止(self, fake_interrupt, answer):
        fake_interrupt.value = answer
        out = hg.risk_gate_node({"need_medical": True, "risk_level": "high"})
        assert out == {"risk_ack": False, "aborted": True}

    @pytest.mark.parametrize("answer", ["???", "", None, "随便应付一下"])
    def test_无法识别时fail_closed(self, fake_interrupt, answer):
        """识别不出意图时按「中止」处理——就医场景里这个方向的代价小得多。"""
        fake_interrupt.value = answer
        assert hg.risk_gate_node({"need_medical": True})["aborted"] is True

    def test_中断载荷具备前端渲染所需字段(self, fake_interrupt):
        hg.risk_gate_node(
            {
                "need_medical": True,
                "risk_level": "high",
                "profile_input": {"height_cm": 170, "weight_kg": 85},
            }
        )
        payload = fake_interrupt.calls[0]
        assert payload["type"] == "risk_confirmation"
        assert payload["question"]
        assert payload["risk_level"] == "high"
        assert payload["bmi"] == 29.4
        assert [o["value"] for o in payload["options"]] == ["continue", "abort"]
        assert all("label" in o for o in payload["options"])

    def test_画像缺失时载荷仍然可渲染(self, fake_interrupt):
        hg.risk_gate_node({"need_medical": True, "risk_level": "high"})
        assert fake_interrupt.calls[0]["bmi"] is None


# ---------------------------------------------------------------- 确认闸门
class TestConfirmGate:
    def test_接受计划(self, fake_interrupt):
        fake_interrupt.value = {"decision": "accept"}
        assert hg.confirm_gate_node({"revision_count": 0, "message": "原消息"}) == {
            "revision_request": ""
        }

    def test_要求调整并带上意见(self, fake_interrupt):
        fake_interrupt.value = {"decision": "revise", "feedback": "蛋白质再高一点"}
        out = hg.confirm_gate_node({"revision_count": 0, "message": "原消息"})
        assert out["revision_count"] == 1
        assert out["revision_request"] == "蛋白质再高一点"
        # message 会被 adjust_planner 当作调整需求读取，必须一起换掉
        assert out["message"] == "蛋白质再高一点"

    def test_要求调整但没写意见时沿用原消息(self, fake_interrupt):
        fake_interrupt.value = "revise"
        assert hg.confirm_gate_node({"revision_count": 0, "message": "原消息"})["message"] == "原消息"

    @pytest.mark.parametrize("answer", ["我想改一下运动部分", "调整强度", "不满意，重来", "换个方案"])
    def test_中文口语被识别为调整(self, fake_interrupt, answer):
        fake_interrupt.value = answer
        out = hg.confirm_gate_node({"revision_count": 0, "message": "m"})
        assert out["revision_count"] == 1, f"{answer!r} 应被识别为要求调整"

    def test_轮数递增(self, fake_interrupt):
        fake_interrupt.value = {"decision": "revise", "feedback": "x"}
        out = hg.confirm_gate_node({"revision_count": 2, "message": "m"})
        assert out["revision_count"] == 3

    def test_达到上限后不再打断(self, fake_interrupt):
        """防死循环：到达 MAX_REVISIONS 后闸门直接放行，不再询问。"""
        out = hg.confirm_gate_node({"revision_count": MAX_REVISIONS, "message": "m"})
        assert out == {"revision_request": ""}
        assert fake_interrupt.calls == []

    @pytest.mark.parametrize("count", [MAX_REVISIONS + 1, MAX_REVISIONS + 99])
    def test_超过上限也不会反复打断(self, fake_interrupt, count):
        hg.confirm_gate_node({"revision_count": count, "message": "m"})
        assert fake_interrupt.calls == []

    def test_上限前一轮仍会打断(self, fake_interrupt):
        fake_interrupt.value = "accept"
        hg.confirm_gate_node({"revision_count": MAX_REVISIONS - 1, "message": "m"})
        assert len(fake_interrupt.calls) == 1

    def test_中断载荷带上剩余轮数(self, fake_interrupt):
        fake_interrupt.value = "accept"
        hg.confirm_gate_node({"revision_count": 1, "message": "m"})
        payload = fake_interrupt.calls[0]
        assert payload["type"] == "plan_confirmation"
        assert payload["revision_count"] == 1
        assert payload["max_revisions"] == MAX_REVISIONS
        assert str(MAX_REVISIONS - 1) in payload["hint"]


# ---------------------------------------------------------------- 路由
class TestRouting:
    def test_风险闸门路由(self):
        assert hg.route_after_risk_gate({"aborted": True}) == "abort"
        assert hg.route_after_risk_gate({"aborted": False}) == "plan"

    def test_风险闸门缺少字段时默认放行(self):
        assert hg.route_after_risk_gate({}) == "plan"

    def test_确认闸门路由(self):
        assert hg.route_after_confirm({"revision_request": "改一下"}) == "revise"
        assert hg.route_after_confirm({"revision_request": ""}) == "accept"

    def test_确认闸门缺少字段时默认结束(self):
        assert hg.route_after_confirm({}) == "accept"


# ---------------------------------------------------------------- 中止分支
class TestAbortNode:
    def test_中止产出只有就医建议没有计划(self):
        out = hg.abort_node(
            {
                "profile_input": {"height_cm": 170, "weight_kg": 85},
                "risk_level": "high",
                "assessment": "评估报告正文",
            }
        )
        obj = json.loads(out["final_json"])
        assert obj["meta"]["aborted"] is True
        assert obj["risk"]["need_medical"] is True
        assert obj["profile"]["bmi"] == 29.4
        assert "diet_plan" not in obj
        assert "exercise_plan" not in obj
        assert obj["next_steps"]
        assert out["aborted"] is True

    def test_中止分支同样强制注入免责声明(self):
        """护栏在任何分支上都不能少——包括「什么都不做」这条分支。"""
        out = hg.abort_node({})
        obj = json.loads(out["final_json"])
        assert obj["meta"]["disclaimer"] == DISCLAIMER
        assert obj["next_steps"]

    def test_画像为空时不崩溃(self):
        # 回归：rule_based_risk 过去会在脏输入上抛 ValueError 打穿护栏
        out = hg.abort_node({"profile_input": {"height_cm": "1.7米", "weight_kg": "65公斤"}})
        assert json.loads(out["final_json"])["profile"]["bmi"] is None
