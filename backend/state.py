"""图状态定义（节点间传递的数据）与初始状态构造。"""
from __future__ import annotations

from typing import TypedDict


class State(TypedDict):
    # 输入
    message: str          # 当前用户消息（自然语言）
    session_id: str       # 会话 ID（短期记忆 key）
    user_id: str          # 用户 ID（长期记忆 key）
    history: list         # 短期记忆：当前会话多轮对话 [{role, content}]
    last_plan: str        # 长期记忆：上一版计划 JSON 字符串
    profile_input: dict   # 结构化画像（generate 时由画像解析提取；adjust 时从长期记忆加载）
    user_context: str     # 长期记忆文本（历史体重趋势 + 旧画像），供健康评估引用
    intent: str           # generate（首次生成） / adjust（追问调整）
    # 中间产物
    profile: str          # 画像文本
    assessment: str       # 健康评估报告
    risk_level: str       # low / medium / high
    need_medical: bool    # 是否建议就医
    diet_plan: str        # 膳食规划
    exercise_plan: str    # 运动规划
    lifestyle_plan: str   # 作息规划
    # 输出
    final_json: str       # 最终 JSON 字符串


def initial_state(
    message: str,
    session_id: str = "",
    user_id: str = "default",
    history: list | None = None,
    last_plan: str = "",
    profile_input: dict | None = None,
    user_context: str = "",
    intent: str = "generate",
) -> dict:
    """构造完整初始状态。"""
    return {
        "message": message,
        "session_id": session_id,
        "user_id": user_id,
        "history": history or [],
        "last_plan": last_plan,
        "profile_input": profile_input or {},
        "user_context": user_context,
        "intent": intent,
        "profile": "",
        "assessment": "",
        "risk_level": "low",
        "need_medical": False,
        "diet_plan": "",
        "exercise_plan": "",
        "lifestyle_plan": "",
        "final_json": "",
    }
