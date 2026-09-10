"""健康工具 MCP 服务器（本地 mock 数据，不接真实 API）。

对外提供的工具：
1. calculate_bmi        —— 根据身高体重计算 BMI 与分类
2. query_health_knowledge —— 检索本地健康知识库
3. query_food_nutrition —— 查询食物营养
4. get_exercise_guidance —— 按目标与风险返回运动指南
5. get_weather          —— 返回 mock 天气
6. get_sleep_guidance   —— 返回睡眠作息建议

运行：由 health_graph.py 通过 stdio 传输自动拉起，无需手动运行。
注意：本服务器内禁止 print（stdio 是二进制协议，任何 print 都会污染握手）。
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("健康工具")

HEALTH_KNOWLEDGE = {
    "减脂": "减脂核心是热量缺口：摄入<消耗，建议每日缺口 300-500kcal，有氧+力量结合，保证蛋白质防肌肉流失。",
    "增肌": "增肌需要热量盈余+渐进超负荷力量训练，每日蛋白质 1.6-2.2g/kg 体重，训练后及时补充碳水和蛋白质。",
    "养生": "养生重在规律作息、均衡饮食、适度运动、情绪稳定，避免熬夜与高油高糖，多食蔬果与全谷物。",
    "高血压": "高血压应低盐饮食（<5g/天）、控制体重、规律有氧运动、戒烟限酒、遵医嘱用药，避免剧烈憋气运动。",
    "糖尿病": "糖尿病需控制碳水总量、优先低GI食物、定时定量进餐、监测血糖，运动前后防低血糖。",
    "心脏病": "心脏病运动需医生评估，以低强度有氧为主（快走/太极），避免剧烈与憋气，胸闷气短立即停止。",
}

FOOD_NUTRITION = {
    "鸡胸肉": "每100g：热量133kcal，蛋白质24g，碳水0g，脂肪3g。低脂高蛋白，适合减脂增肌。",
    "鸡蛋": "每100g：热量144kcal，蛋白质13g，碳水2g，脂肪9g。优质蛋白，营养全面。",
    "牛奶": "每100g：热量54kcal，蛋白质3g，碳水5g，脂肪3g。补钙优质来源。",
    "糙米": "每100g：热量112kcal，蛋白质2.6g，碳水24g，脂肪0.9g。低GI主食，饱腹感强。",
    "西兰花": "每100g：热量34kcal，蛋白质2.8g，碳水7g，脂肪0.4g。高纤维低热量蔬菜。",
    "三文鱼": "每100g：热量208kcal，蛋白质20g，碳水0g，脂肪13g。富含Omega-3。",
    "燕麦": "每100g：热量367kcal，蛋白质13g，碳水60g，脂肪7g。高纤维低GI主食。",
    "红薯": "每100g：热量86kcal，蛋白质1.6g，碳水20g，脂肪0.1g。优质复合碳水。",
    "豆腐": "每100g：热量84kcal，蛋白质8g，碳水3g，脂肪4g。植物蛋白好来源。",
    "苹果": "每100g：热量52kcal，蛋白质0.3g，碳水14g，脂肪0.2g。低热量水果。",
}

EXERCISE_GUIDANCE = {
    ("减脂", "low"): "中低强度有氧为主：快走/慢跑/游泳，每周4-5次，每次30-45分钟，搭配每周2次力量训练保肌。",
    ("减脂", "medium"): "有氧+力量结合：每周4次，有氧30-40分钟（慢跑/骑行/跳绳）+力量20-30分钟，注意热身拉伸。",
    ("减脂", "high"): "仅建议低强度活动：散步/太极/舒缓瑜伽，每次20-30分钟，运动前请咨询医生，避免憋气与剧烈运动。",
    ("增肌", "low"): "力量训练为主：每周4-5次，每次45-60分钟，覆盖大肌群，配合充足蛋白与睡眠。",
    ("增肌", "medium"): "力量训练为主：每周4-5次，渐进增加负荷，每次45-60分钟，练后补充蛋白+碳水。",
    ("增肌", "high"): "仅建议低强度：散步/太极，每次20-30分钟，避免大重量憋气，运动前请咨询医生。",
    ("养生", "low"): "温和有氧+柔韧：太极/八段锦/快走/瑜伽，每周3-5次，每次30分钟，重在坚持。",
    ("养生", "medium"): "温和有氧+柔韧：快走/骑行/瑜伽，每周3-5次，每次30-40分钟，量力而行。",
    ("养生", "high"): "仅建议低强度：散步/舒缓瑜伽，每次20分钟，避免劳累，运动前请咨询医生。",
}

SLEEP_GUIDANCE = {
    "减脂": "建议 7-8 小时睡眠，23:00 前入睡、06:30 左右起床。睡眠不足会升高饥饿素、不利于减脂。",
    "增肌": "建议 7-9 小时睡眠，23:00 前入睡。生长激素主要在深睡期分泌，充足睡眠是增肌关键。",
    "养生": "建议 7-8 小时睡眠，22:30-23:00 入睡，顺应自然节律，午间可小憩 20 分钟。",
}


@mcp.tool()
def calculate_bmi(height_cm: float, weight_kg: float) -> str:
    """根据身高(cm)和体重(kg)计算 BMI 并给出分类。"""
    bmi = round(weight_kg / ((height_cm / 100) ** 2), 1)
    if bmi < 18.5:
        cat = "偏瘦"
    elif bmi < 24:
        cat = "正常"
    elif bmi < 28:
        cat = "超重"
    else:
        cat = "肥胖"
    return f"BMI={bmi}, 分类={cat}"


@mcp.tool()
def query_health_knowledge(query: str) -> str:
    """在本地健康知识库中检索与 query 相关的科普要点，找不到返回"未收录"。"""
    hits = [v for k, v in HEALTH_KNOWLEDGE.items() if k in query or query in k]
    if not hits:
        return "未收录与该查询相关的健康知识。"
    return "\n".join(hits)


@mcp.tool()
def query_food_nutrition(food: str) -> str:
    """查询某食物每 100g 的热量/蛋白质/碳水/脂肪，找不到返回"未收录"。"""
    for k, v in FOOD_NUTRITION.items():
        if food in k or k in food:
            return v
    return f"未收录「{food}」的营养数据。"


@mcp.tool()
def get_exercise_guidance(goal: str, risk_level: str) -> str:
    """按健康目标(减脂/增肌/养生)与风险等级(low/medium/high)返回运动指南。"""
    if (goal, risk_level) in EXERCISE_GUIDANCE:
        return EXERCISE_GUIDANCE[(goal, risk_level)]
    for (g, _lvl), v in EXERCISE_GUIDANCE.items():
        if g == goal:
            return v
    return "未收录该目标的运动指南。"


@mcp.tool()
def get_weather(city: str) -> str:
    """返回指定城市今日 mock 天气及是否适合户外运动。"""
    return f"{city}：晴，22°C，空气质量优，适合户外运动。"


@mcp.tool()
def get_sleep_guidance(goal: str) -> str:
    """按健康目标返回睡眠时长与作息建议。"""
    return SLEEP_GUIDANCE.get(goal, "建议 7-8 小时睡眠，23:00 前入睡。")


if __name__ == "__main__":
    mcp.run()
