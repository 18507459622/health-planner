"""记忆系统：短期（会话级）+ 长期（用户级）分层记忆。

- 短期记忆：session:{sid}:messages —— 当前会话的多轮对话，TTL 1 小时。
- 长期记忆：user:{uid}:profile / weight_history / last_plan —— 用户画像、体重历史、
  上一版计划，跨会话持久化。

存储引擎：默认用 fakeredis（纯 Python 内存模拟，redis-py 标准 API），
通过环境变量 REDIS_URL 可无缝切换到真 Redis（生产用）。
"""

import json
import os
from datetime import date

import fakeredis
import redis

DEFAULT_USER = "default"
_SESSION_TTL = 3600  # 短期记忆 1 小时

_client = None


def get_redis():
    """返回 Redis 兼容客户端：有 REDIS_URL 用真 Redis，否则用 fakeredis 内存引擎。"""
    url = os.getenv("REDIS_URL", "")
    if url:
        return redis.from_url(url, decode_responses=True)
    return fakeredis.FakeRedis(decode_responses=True)


def _r():
    global _client
    if _client is None:
        _client = get_redis()
    return _client


# ---------- 短期记忆（会话级） ----------
def append_message(session_id: str, role: str, content: str) -> None:
    key = f"session:{session_id}:messages"
    _r().rpush(key, json.dumps({"role": role, "content": content}, ensure_ascii=False))
    _r().expire(key, _SESSION_TTL)


def get_history(session_id: str, limit: int = 10) -> list:
    key = f"session:{session_id}:messages"
    items = _r().lrange(key, -limit, -1)
    return [json.loads(x) for x in items]


# ---------- 长期记忆（用户级） ----------
def save_profile(user_id: str, profile: dict) -> None:
    _r().set(f"user:{user_id}:profile", json.dumps(profile, ensure_ascii=False))


def get_profile(user_id: str) -> dict | None:
    raw = _r().get(f"user:{user_id}:profile")
    return json.loads(raw) if raw else None


def append_weight(user_id: str, weight: float) -> None:
    key = f"user:{user_id}:weight_history"
    _r().rpush(key, json.dumps({"weight": weight, "date": date.today().isoformat()},
                               ensure_ascii=False))


def get_weight_history(user_id: str, limit: int = 10) -> list:
    items = _r().lrange(f"user:{user_id}:weight_history", -limit, -1)
    return [json.loads(x) for x in items]


def save_last_plan(user_id: str, plan_json: str) -> None:
    _r().set(f"user:{user_id}:last_plan", plan_json)


def get_last_plan(user_id: str) -> str | None:
    return _r().get(f"user:{user_id}:last_plan")
