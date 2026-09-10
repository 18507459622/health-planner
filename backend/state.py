"""图状态定义（节点间传递的数据）与初始状态构造。"""
from __future__ import annotations

from typing import TypedDict


class State(TypedDict):
    # 输入
    profile_raw: str      # 供 LLM 阅读的文本描述（由结构化输入格式化而来）
    profile_input: dict   # 结构化输入（确定性规则计算用，如 BMI/疾病识别）
    # 中间产物
    profile: str          # 画像解析结果
    assessment: str       # 健康评估报告
    risk_level: str       # low / medium / high
    need_medical: bool    # 是否建议就医
    diet_plan: str        # 膳食规划
    exercise_plan: str    # 运动规划
    lifestyle_plan: str   # 作息规划
    # 输出
    final_json: str       # 最终 JSON 字符串


def format_profile(p: dict) -> str:
    """把结构化输入格式化成一段文本，供 LLM 阅读。"""
    diseases = "、".join(d for d in (p.get("diseases") or []) if d) or "无"
    parts = [
        f"性别：{p.get('gender', '未知')}",
        f"年龄：{p.get('age', '未知')}",
        f"身高：{p.get('height_cm', '未知')}cm",
        f"体重：{p.get('weight_kg', '未知')}kg",
        f"健康目标：{p.get('goal', '未知')}",
        f"基础疾病：{diseases}",
        f"所在城市：{p.get('city', '未知')}",
    ]
    return "\n".join(parts)


def initial_state(profile_input: dict) -> dict:
    """构造完整初始状态。"""
    return {
        "profile_raw": format_profile(profile_input),
        "profile_input": profile_input,
        "profile": "",
        "assessment": "",
        "risk_level": "low",
        "need_medical": False,
        "diet_plan": "",
        "exercise_plan": "",
        "lifestyle_plan": "",
        "final_json": "",
    }
