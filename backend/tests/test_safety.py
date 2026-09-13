"""safety.py 单元测试 —— 安全护栏是整套系统里最该被测试的部分。

为什么先测这个文件
------------------
安全护栏是纯函数：无 IO、无 LLM 依赖、无网络，却能脱离一切外部条件跑；
但它又是"高风险场景不能出错"的最后一道闸——
graph 里 health_assessor_node 用 `stricter_level(规则层, LLM层)` 取更严者兜底，
所以 safety.py 里任何一条退化，都会被放大成医疗安全事件。

覆盖范围
--------
- rule_based_risk：BMI 五档分级与三处边界、疾病强制升级、脏输入容错
- stricter_level ：取更严者、交换律、非法等级退化（标注缺口）
- parse_risk     ：报告抽取、未命中时 fail-open（标注缺口）
- extract_json   ：代码围栏 / 前后噪声 / 截断 / 多段 JSON
- ensure_disclaimer：无条件覆盖
- 组合语义       ：复刻 health_assessor_node 的兜底逻辑

标了 `gap` 的用例记录的是"当前行为即缺口"，不是期望行为。
跑 `pytest -m gap` 可以一次列出全部待修项。
"""
from __future__ import annotations

import pytest

from safety import (
    DISCLAIMER,
    DISEASE_KEYWORDS,
    ensure_disclaimer,
    extract_json,
    parse_risk,
    rule_based_risk,
    stricter_level,
)


def profile(**overrides) -> dict:
    """一份正常的用户画像，用例只覆盖自己关心的字段。"""
    base = {"height_cm": 170, "weight_kg": 65, "diseases": [], "goal": "减脂"}
    base.update(overrides)
    return base


# ---------------------------------------------------------------- BMI 分级
class TestRuleBasedRiskBMI:
    def test_正常体重为低风险(self):
        r = rule_based_risk(profile(weight_kg=65))
        assert r["bmi"] == pytest.approx(22.5, abs=0.05)
        assert r["risk_level"] == "low"
        assert r["need_medical"] is False

    @pytest.mark.parametrize(
        "weight,expect_bmi,expect_level,expect_need",
        [
            (50.0, 17.3, "high", True),  # BMI < 18.5 偏瘦 → 高风险
            (53.5, 18.5, "low", False),  # 下边界：18.5 不算偏瘦
            (69.4, 24.0, "medium", False),  # 下边界：24.0 算超重
            (75.0, 26.0, "medium", False),
            (80.9, 28.0, "high", True),  # 下边界：28.0 算肥胖 → 高风险
            (85.0, 29.4, "high", True),
        ],
    )
    def test_BMI分级边界(self, weight, expect_bmi, expect_level, expect_need):
        r = rule_based_risk(profile(weight_kg=weight))
        assert r["bmi"] == pytest.approx(expect_bmi, abs=0.05)
        assert r["risk_level"] == expect_level
        assert r["need_medical"] is expect_need

    def test_高风险BMI才要求就医(self):
        # 超重（24≤BMI<28）只是告警，不触发就医
        assert rule_based_risk(profile(weight_kg=75))["need_medical"] is False
        # 偏瘦与肥胖都触发就医
        assert rule_based_risk(profile(weight_kg=50))["need_medical"] is True
        assert rule_based_risk(profile(weight_kg=85))["need_medical"] is True

    def test_BMI保留一位小数(self):
        r = rule_based_risk(profile(height_cm=173, weight_kg=68))
        assert r["bmi"] == round(68 / ((173 / 100) ** 2), 1)


# ---------------------------------------------------------------- 疾病
class TestRuleBasedRiskDiseases:
    def test_基础疾病强制升级为高风险(self):
        r = rule_based_risk(profile(diseases=["高血压"]))
        assert r["risk_level"] == "high"
        assert r["need_medical"] is True
        assert r["diseases"] == ["高血压"]

    def test_疾病结论覆盖正常BMI的结论(self):
        # BMI 22.5 本应判 low，但基础疾病必须把风险顶到 high
        r = rule_based_risk(profile(weight_kg=65, diseases=["糖尿病"]))
        assert r["bmi"] == pytest.approx(22.5, abs=0.05)
        assert r["risk_level"] == "high"
        assert r["need_medical"] is True

    @pytest.mark.parametrize("diseases", [["无"], [None], [""], [None, ""], []])
    def test_空疾病占位被过滤(self, diseases):
        r = rule_based_risk(profile(diseases=diseases))
        assert r["diseases"] == []
        assert r["risk_level"] == "low"
        assert r["need_medical"] is False

    def test_混入空值时真疾病仍被识别(self):
        r = rule_based_risk(profile(diseases=["无", None, "高血压", ""]))
        assert r["diseases"] == ["高血压"]
        assert r["risk_level"] == "high"

    def test_全部疾病关键词都能触发高风险(self):
        for d in sorted(DISEASE_KEYWORDS):
            r = rule_based_risk(profile(diseases=[d]))
            assert r["risk_level"] == "high", f"疾病关键词 {d} 未触发高风险"
            assert r["need_medical"] is True, f"疾病关键词 {d} 未触发就医"


# ---------------------------------------------------------------- 脏输入
class TestRuleBasedRiskDirtyInput:
    @pytest.mark.parametrize("bad", ["abc", "1.7米", "170cm", "未知", "一百七十"])
    def test_非数值身高不崩溃(self, bad):
        """回归用例：护栏本身绝不能抛异常把 graph 打穿。

        原实现 `except (TypeError, ZeroDivisionError)` 漏了 ValueError，
        而 float("1.7米") 抛的正是 ValueError——
        profile_parser_node 不做类型强转，用户说"我身高一米七"就能触发。
        """
        r = rule_based_risk(profile(height_cm=bad))
        assert r["bmi"] is None
        assert r["risk_level"] == "low"
        assert r["need_medical"] is False

    @pytest.mark.parametrize("bad", ["abc", "六十五公斤", ""])
    def test_非数值体重不崩溃(self, bad):
        r = rule_based_risk(profile(weight_kg=bad))
        assert r["bmi"] is None

    def test_身高为0不崩溃(self):
        # 0 是 falsy，会走不到除法；这里同时覆盖 (x/100)**2 == 0 的除零路径
        r = rule_based_risk(profile(height_cm=0, weight_kg=65))
        assert r["bmi"] is None
        assert r["risk_level"] == "low"

    def test_数值字符串可正常计算(self):
        r = rule_based_risk(profile(height_cm="170", weight_kg="65"))
        assert r["bmi"] == pytest.approx(22.5, abs=0.05)

    def test_负数身高不崩溃(self):
        r = rule_based_risk(profile(height_cm=-170, weight_kg=65))
        assert r["bmi"] is not None  # 数学上能算出来，本层不做合理性校验

    @pytest.mark.parametrize("empty", [{}, {"height_cm": None, "weight_kg": None}, {"diseases": None}])
    def test_空画像返回保守默认(self, empty):
        assert rule_based_risk(empty) == {
            "bmi": None,
            "risk_level": "low",
            "need_medical": False,
            "diseases": [],
        }


# ---------------------------------------------------------------- 取更严者
class TestStricterLevel:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("low", "high", "high"),
            ("high", "low", "high"),
            ("low", "medium", "medium"),
            ("medium", "low", "medium"),
            ("medium", "high", "high"),
            ("high", "medium", "high"),
            ("low", "low", "low"),
            ("medium", "medium", "medium"),
            ("high", "high", "high"),
        ],
    )
    def test_取更严的一档(self, a, b, expected):
        assert stricter_level(a, b) == expected

    def test_对合法等级满足交换律(self):
        levels = ("low", "medium", "high")
        for a in levels:
            for b in levels:
                assert stricter_level(a, b) == stricter_level(b, a), f"({a},{b}) 不对称"

    @pytest.mark.gap
    def test_非法等级出现在左侧时反而胜出(self):
        """缺口：_SEVERITY.get(未知, 0) 把未知等级当 0，又因为它 >= 右侧的 0，
        于是"未知"被原样返回，下游会拿一个非法等级去做判断。"""
        assert stricter_level("bogus", "low") == "bogus"
        assert stricter_level("高危", "low") == "高危"

    @pytest.mark.gap
    def test_非法等级出现在右侧时被静默丢弃(self):
        """同一缺口的另一侧：非法值在右边就被丢掉，两个方向行为不对称。"""
        assert stricter_level("low", "极高") == "low"


# ---------------------------------------------------------------- 报告解析
class TestParseRisk:
    @pytest.mark.parametrize(
        "report,expected",
        [
            ("风险等级：high", "high"),
            ("风险等级:高", "high"),
            ("风险等级：HIGH", "high"),
            ("风险等级：medium", "medium"),
            ("风险等级：中", "medium"),
            ("风险等级：low", "low"),
            ("风险等级：低", "low"),
            ("**风险等级：高**", "high"),
            ("风险等级：   高", "high"),
        ],
    )
    def test_抽取风险等级(self, report, expected):
        assert parse_risk(report)[0] == expected

    def test_高风险优先于低风险(self):
        assert parse_risk("风险等级：低\n后来又改成 风险等级：高")[0] == "high"

    @pytest.mark.parametrize(
        "report", ["是否需要就医：是", "建议就医：需要", "是否需要就医：true", "是否需要就医：建议"]
    )
    def test_识别需要就医(self, report):
        assert parse_risk(report)[1] is True

    @pytest.mark.parametrize(
        "report", ["是否需要就医：否", "是否需要就医：不需要", "无需就医", "没有这一项", ""]
    )
    def test_未标记需要就医则为假(self, report):
        assert parse_risk(report)[1] is False

    def test_返回值恒为二元组(self):
        level, need = parse_risk("任意文本")
        assert level in ("low", "medium", "high")
        assert isinstance(need, bool)

    @pytest.mark.gap
    def test_未命中时默认低风险_方向是fail_open(self):
        """缺口（本文件最重要的一条）：解析失败默认 low/False，方向是 fail-open。

        目前靠 graph 里 stricter_level(规则层, LLM层) 兜住才没出事，
        但这是"让调用方补救"，护栏自身应当 fail-closed。
        """
        assert parse_risk("模型这次没按格式输出") == ("low", False)

    @pytest.mark.gap
    def test_未收录的枚举值被静默降级(self):
        """缺口：报告写"极高"时正则不匹配，静默降成 low —— 比不解析更危险，
        因为它看起来像是"解析成功了，结论是低风险"。"""
        assert parse_risk("风险等级：极高") == ("low", False)


# ---------------------------------------------------------------- JSON 兜底
class TestExtractJson:
    def test_纯JSON(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_代码围栏带语言标记(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_代码围栏无语言标记(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_前后带解释文字(self):
        assert extract_json('好的，这是为你生成的计划：{"a": 1} 请查收') == {"a": 1}

    def test_嵌套对象(self):
        assert extract_json('{"a": {"b": 2}, "c": [1, 2]}') == {"a": {"b": 2}, "c": [1, 2]}

    def test_中文原样保留(self):
        assert extract_json('{"目标": "减脂"}') == {"目标": "减脂"}

    @pytest.mark.parametrize("bad", ["", None, "没有大括号", '{"a":', "null", "   "])
    def test_解析失败一律返回None(self, bad):
        assert extract_json(bad) is None

    @pytest.mark.gap
    def test_连续两段JSON无法解析(self):
        """缺口：实现取"第一个 { 到最后一个 }"，模型若先给草稿再给正式结果，
        两段会被拼成一段非法 JSON，最终静默返回 None。"""
        assert extract_json('{"a": 1} 补充说明 {"b": 2}') is None


# ---------------------------------------------------------------- 免责声明
class TestEnsureDisclaimer:
    def test_无条件覆盖已有的假免责声明(self):
        obj = ensure_disclaimer({"meta": {"disclaimer": "假的免责声明"}})
        assert obj["meta"]["disclaimer"] == DISCLAIMER

    def test_保留meta下的其它字段(self):
        obj = ensure_disclaimer({"meta": {"author": "x", "version": 2}, "plan": 1})
        assert obj["meta"]["author"] == "x"
        assert obj["meta"]["version"] == 2
        assert obj["plan"] == 1
        assert obj["meta"]["disclaimer"] == DISCLAIMER

    def test_缺少meta时自动补(self):
        assert ensure_disclaimer({})["meta"]["disclaimer"] == DISCLAIMER

    def test_meta被写成非字典时被覆盖(self):
        obj = ensure_disclaimer({"meta": "不是字典"})
        assert obj["meta"]["disclaimer"] == DISCLAIMER

    @pytest.mark.parametrize("bad", ["纯文本", None, 123, ["列表"]])
    def test_非字典输入被包装(self, bad):
        obj = ensure_disclaimer(bad)
        assert obj["meta"]["disclaimer"] == DISCLAIMER
        assert "raw" in obj

    def test_免责声明内容非空且提到医生(self):
        assert DISCLAIMER.strip()
        assert "医生" in DISCLAIMER
        assert "不构成医疗建议" in DISCLAIMER


# ---------------------------------------------------------------- 组合语义
class Test护栏组合语义:
    """复刻 health_assessor_node 的兜底逻辑，验证两层护栏叠加后的净效果。"""

    def test_规则层高风险不会被LLM的低风险洗掉(self):
        rule = rule_based_risk(profile(diseases=["高血压"]))
        llm_level, llm_need = parse_risk("风险等级：低")
        assert stricter_level(rule["risk_level"], llm_level) == "high"
        assert (rule["need_medical"] or llm_need) is True

    def test_LLM层高风险能顶上去(self):
        rule = rule_based_risk(profile(weight_kg=65))
        llm_level, _ = parse_risk("风险等级：高")
        assert stricter_level(rule["risk_level"], llm_level) == "high"

    def test_LLM解析失败时规则层仍然守得住(self):
        # fail-open 的 parse_risk 在这里是安全的：它只会失败成 low，
        # 而 stricter_level 取更严者，规则层的 high 依然守得住。
        rule = rule_based_risk(profile(diseases=["糖尿病"]))
        llm_level, llm_need = parse_risk("这次模型没按格式输出")
        assert llm_level == "low"
        assert stricter_level(rule["risk_level"], llm_level) == "high"
        assert (rule["need_medical"] or llm_need) is True

    def test_两层都低才是低(self):
        rule = rule_based_risk(profile(weight_kg=65))
        llm_level, llm_need = parse_risk("风险等级：低，是否需要就医：否")
        assert stricter_level(rule["risk_level"], llm_level) == "low"
        assert (rule["need_medical"] or llm_need) is False
