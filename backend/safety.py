"""安全护栏：免责声明 + 规则化风险识别 + JSON 兜底解析。"""
from __future__ import annotations

import json
import re

DISCLAIMER = "本计划为健康科普，不构成医疗建议。如有基础疾病或身体不适，请及时咨询专业医生。"

DISEASE_KEYWORDS = {"高血压", "糖尿病", "心脏病", "冠心病", "哮喘", "肾病", "痛风", "甲状腺"}

_SEVERITY = {"low": 0, "medium": 1, "high": 2}


def rule_based_risk(profile: dict) -> dict:
    """确定性规则预判：根据身高体重算 BMI、识别基础疾病，产出硬结论。"""
    height_cm = profile.get("height_cm")
    weight_kg = profile.get("weight_kg")
    diseases = [d for d in (profile.get("diseases") or []) if d and d != "无"]
    bmi = None
    if height_cm and weight_kg:
        try:
            bmi = round(float(weight_kg) / ((float(height_cm) / 100) ** 2), 1)
        except (TypeError, ZeroDivisionError):
            bmi = None
    level, need_medical = "low", False
    if bmi is not None:
        if bmi < 18.5 or bmi >= 28:
            level, need_medical = "high", True
        elif bmi >= 24:
            level = "medium"
    if diseases:
        level, need_medical = "high", True
    return {"bmi": bmi, "risk_level": level, "need_medical": need_medical, "diseases": diseases}


def stricter_level(a: str, b: str) -> str:
    """取两个风险等级中更严的一档。"""
    return a if _SEVERITY.get(a, 0) >= _SEVERITY.get(b, 0) else b


def parse_risk(report: str) -> tuple[str, bool]:
    """从评估报告文本抽取风险等级与是否需要就医；未命中默认 low/False。"""
    level = "low"
    if re.search(r"风险等级[：:]\s*(high|高)", report, re.I):
        level = "high"
    elif re.search(r"风险等级[：:]\s*(medium|中)", report, re.I):
        level = "medium"
    elif re.search(r"风险等级[：:]\s*(low|低)", report, re.I):
        level = "low"
    need = bool(re.search(r"(是否需要就医|建议就医)[：:]\s*(是|true|需要|建议)", report, re.I))
    return level, need


def extract_json(text: str):
    """从 LLM 输出中截取第一个 { 到最后一个 } 解析为 dict；失败返回 None。"""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return None


def ensure_disclaimer(obj) -> dict:
    """无条件覆盖 meta.disclaimer，保证免责声明 100% 出现。"""
    if not isinstance(obj, dict):
        obj = {"raw": str(obj)}
    obj.setdefault("meta", {})
    obj["meta"]["disclaimer"] = DISCLAIMER
    return obj
