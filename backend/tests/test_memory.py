"""memory.py 单元测试 —— 记忆系统是"多轮"和"个性化"的地基。

为什么先测这个文件
------------------
memory.py 走 fakeredis，可以脱离真 Redis 独立跑，测试成本几乎为零；
但它有两处已知缺口，恰好是"多用户并发"场景下最难排查的那类问题：

1. 长期键（profile / weight_history / last_plan）一个都没设 TTL，会无限增长；
2. save_profile 用 `set` 整体覆盖、没有 CAS 或版本号，并发的"读-改-写"会静默丢字段。

这两条我用标了 `gap` 的用例把当前行为钉死——修复时就有了对照基线。

覆盖范围：短期记忆（rpush/TTL/隔离）、长期记忆（往返/追加）、存储引擎选择、已知缺口。
"""
from __future__ import annotations

import json

import fakeredis
import pytest

import memory


@pytest.fixture(autouse=True)
def fresh_redis(monkeypatch):
    """每个用例一个全新的 fakeredis，并强制忽略 REDIS_URL。

    memory 用模块级 `_client` 做单例缓存，不重置会把上一个用例的数据带进来，
    也会让"空会话返回空列表"之类的用例产生假阳性。
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    memory._client = None
    yield
    memory._client = None


# ---------------------------------------------------------------- 短期记忆
class Test短期记忆:
    def test_追加消息后按序读回(self):
        memory.append_message("s1", "user", "我想减脂")
        memory.append_message("s1", "assistant", "好的，先告诉我身高体重")
        assert memory.get_history("s1") == [
            {"role": "user", "content": "我想减脂"},
            {"role": "assistant", "content": "好的，先告诉我身高体重"},
        ]

    def test_追加不覆盖历史(self):
        for i in range(5):
            memory.append_message("s1", "user", f"第{i}条")
        assert len(memory.get_history("s1", limit=99)) == 5

    def test_limit只取最近N条且保持正序(self):
        for i in range(5):
            memory.append_message("s1", "user", f"第{i}条")
        contents = [m["content"] for m in memory.get_history("s1", limit=2)]
        assert contents == ["第3条", "第4条"]

    def test_limit大于总数时返回全部(self):
        memory.append_message("s1", "user", "只有一条")
        assert len(memory.get_history("s1", limit=99)) == 1

    def test_空会话返回空列表(self):
        assert memory.get_history("不存在的会话") == []

    def test_短期记忆设置了过期时间(self):
        memory.append_message("s1", "user", "hi")
        ttl = memory._r().ttl("session:s1:messages")
        assert 0 < ttl <= 3600, f"短期记忆应有过期时间，实际 ttl={ttl}"

    def test_每次追加都会续期(self):
        memory.append_message("s1", "user", "第一次")
        memory._r().expire("session:s1:messages", 10)  # 手动压短
        memory.append_message("s1", "user", "第二次")
        assert memory._r().ttl("session:s1:messages") > 10

    def test_会话之间互不串扰(self):
        memory.append_message("s1", "user", "A")
        memory.append_message("s2", "user", "B")
        assert [m["content"] for m in memory.get_history("s1")] == ["A"]
        assert [m["content"] for m in memory.get_history("s2")] == ["B"]

    def test_中文与emoji原样保留(self):
        text = "🍎 热量 133kcal —— 「鸡胸肉」"
        memory.append_message("s1", "user", text)
        assert memory.get_history("s1")[0]["content"] == text

    def test_消息结构固定为role加content(self):
        memory.append_message("s1", "user", "hi")
        assert set(memory.get_history("s1")[0]) == {"role", "content"}


# ---------------------------------------------------------------- 长期记忆
class Test长期记忆:
    def test_画像往返一致(self):
        p = {"gender": "男", "goal": "减脂", "diseases": ["高血压"], "height_cm": 170}
        memory.save_profile("u1", p)
        assert memory.get_profile("u1") == p

    def test_未保存的画像返回None(self):
        assert memory.get_profile("从没存过的用户") is None

    def test_重复保存覆盖旧画像(self):
        memory.save_profile("u1", {"goal": "减脂"})
        memory.save_profile("u1", {"goal": "增肌"})
        assert memory.get_profile("u1") == {"goal": "增肌"}

    def test_体重历史按时间追加(self):
        memory.append_weight("u1", 70.5)
        memory.append_weight("u1", 69.8)
        hist = memory.get_weight_history("u1")
        assert [h["weight"] for h in hist] == [70.5, 69.8]

    def test_体重记录带当天日期(self):
        memory.append_weight("u1", 70.0)
        entry = memory.get_weight_history("u1")[0]
        assert set(entry) == {"weight", "date"}
        assert len(entry["date"]) == 10 and entry["date"].count("-") == 2

    def test_体重历史不覆盖也不截断(self):
        for w in (70.0, 69.5, 69.0):
            memory.append_weight("u1", w)
        assert len(memory.get_weight_history("u1", limit=99)) == 3

    def test_上版计划往返一致(self):
        plan = json.dumps({"summary": "减脂计划", "weeks": 4}, ensure_ascii=False)
        memory.save_last_plan("u1", plan)
        assert memory.get_last_plan("u1") == plan
        assert json.loads(memory.get_last_plan("u1"))["summary"] == "减脂计划"

    def test_未保存的计划返回None(self):
        assert memory.get_last_plan("从没存过的用户") is None

    def test_长期记忆跨会话可见(self):
        memory.save_profile("u1", {"goal": "增肌"})
        memory.append_message("s1", "user", "换个会话")
        assert memory.get_profile("u1") == {"goal": "增肌"}

    def test_用户之间互不串扰(self):
        memory.save_profile("u1", {"goal": "减脂"})
        memory.save_profile("u2", {"goal": "增肌"})
        assert memory.get_profile("u1") == {"goal": "减脂"}
        assert memory.get_profile("u2") == {"goal": "增肌"}


# ---------------------------------------------------------------- 引擎选择
class Test存储引擎选择:
    def test_默认使用fakeredis(self):
        assert isinstance(memory.get_redis(), fakeredis.FakeRedis)

    def test_配置REDIS_URL时切换到真Redis客户端(self):
        # redis.from_url 是惰性的，构造客户端不会发起连接，因此无需真服务
        import os

        os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
        try:
            assert not isinstance(memory.get_redis(), fakeredis.FakeRedis)
        finally:
            os.environ.pop("REDIS_URL", None)

    def test_客户端被单例缓存(self):
        assert memory._r() is memory._r()


# ---------------------------------------------------------------- 已知缺口
class Test已知缺口:
    @pytest.mark.gap
    def test_长期键没有设置过期时间(self):
        """缺口：短期记忆设了 1 小时 TTL，三个长期键一个都没设，会无限增长。

        redis 的 TTL 约定：-1 = 键存在但永不过期，-2 = 键不存在。
        """
        memory.save_profile("u1", {"goal": "减脂"})
        memory.append_weight("u1", 70.0)
        memory.save_last_plan("u1", "{}")
        assert memory._r().ttl("user:u1:profile") == -1
        assert memory._r().ttl("user:u1:weight_history") == -1
        assert memory._r().ttl("user:u1:last_plan") == -1

    @pytest.mark.gap
    def test_画像保存是整体覆盖_并发会丢更新(self):
        """缺口：save_profile 用 `set` 整体覆盖，没有 CAS / 版本号 / 字段级合并。

        两个请求同时"读-改-写"时，后写的会静默吃掉先写的字段——
        表现为"我明明改了体重，但资料没更新"，且日志里看不出任何异常。
        """
        memory.save_profile("u1", {"age": 28, "goal": "减脂"})

        a = memory.get_profile("u1")  # 请求 A 读
        b = memory.get_profile("u1")  # 请求 B 读（看到同一份）
        a["age"] = 29
        memory.save_profile("u1", a)  # A 写
        b["goal"] = "增肌"
        memory.save_profile("u1", b)  # B 写

        assert memory.get_profile("u1") == {"age": 28, "goal": "增肌"}  # A 的 age=29 丢了

    @pytest.mark.gap
    def test_体重历史同样没有过期时间(self):
        """缺口：体重历史是最容易无限增长的一个键——每记录一次体重追加一条。"""
        for w in range(60):
            memory.append_weight("u1", 70 + w / 10)
        assert len(memory.get_weight_history("u1", limit=999)) == 60
        assert memory._r().ttl("user:u1:weight_history") == -1
