<template>
  <el-card class="plan-card">
    <template #header>
      <span>📋 健康计划</span>
    </template>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="概览" name="overview">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="健康目标">{{ plan.meta.goal }}</el-descriptions-item>
          <el-descriptions-item label="BMI">{{ plan.profile.bmi }}（{{ plan.profile.bmi_category }}）</el-descriptions-item>
          <el-descriptions-item label="性别/年龄">{{ plan.profile.gender }} / {{ plan.profile.age }}岁</el-descriptions-item>
          <el-descriptions-item label="身高/体重">{{ plan.profile.height_cm }}cm / {{ plan.profile.weight_kg }}kg</el-descriptions-item>
          <el-descriptions-item label="风险等级">{{ riskLabel }}</el-descriptions-item>
          <el-descriptions-item label="是否就医">{{ plan.risk.need_medical ? '建议' : '暂不需要' }}</el-descriptions-item>
        </el-descriptions>
      </el-tab-pane>

      <el-tab-pane label="饮食" name="diet">
        <p><strong>目标热量：</strong>{{ plan.diet.target_calories_kcal }} kcal</p>
        <p>
          <strong>宏量配比：</strong>蛋白质 {{ plan.diet.macro.protein_g }}g / 碳水
          {{ plan.diet.macro.carb_g }}g / 脂肪 {{ plan.diet.macro.fat_g }}g
        </p>
        <p><strong>原则：</strong>{{ (plan.diet.principles || []).join('、') }}</p>
        <el-descriptions :column="1" border class="meals">
          <el-descriptions-item label="早餐">{{ plan.diet.sample_meals?.breakfast }}</el-descriptions-item>
          <el-descriptions-item label="午餐">{{ plan.diet.sample_meals?.lunch }}</el-descriptions-item>
          <el-descriptions-item label="晚餐">{{ plan.diet.sample_meals?.dinner }}</el-descriptions-item>
        </el-descriptions>
        <p><strong>避免：</strong>{{ (plan.diet.avoid || []).join('、') }}</p>
      </el-tab-pane>

      <el-tab-pane label="运动" name="exercise">
        <p><strong>每周频率：</strong>{{ plan.exercise.weekly_frequency }} 次</p>
        <el-table :data="plan.exercise.weekly_plan || []" border size="small">
          <el-table-column prop="day" label="星期" width="90" />
          <el-table-column prop="type" label="类型" />
          <el-table-column prop="duration_min" label="时长(分钟)" width="100" />
          <el-table-column prop="intensity" label="强度" width="80" />
        </el-table>
        <p class="notes"><strong>注意：</strong>{{ (plan.exercise.notes || []).join('、') }}</p>
      </el-tab-pane>

      <el-tab-pane label="作息" name="lifestyle">
        <p>
          <strong>睡眠：</strong>{{ plan.lifestyle.sleep?.target_hours }} 小时（{{
            plan.lifestyle.sleep?.sleep_time
          }}
          - {{ plan.lifestyle.sleep?.wake_time }}）
        </p>
        <p><strong>日常节律：</strong>{{ (plan.lifestyle.routine || []).join('、') }}</p>
        <p><strong>压力管理：</strong>{{ (plan.lifestyle.stress_management || []).join('、') }}</p>
      </el-tab-pane>

      <el-tab-pane label="风险" name="risk">
        <p><strong>风险等级：</strong>{{ riskLabel }}</p>
        <p><strong>说明：</strong>{{ (plan.risk.notes || []).join('；') }}</p>
      </el-tab-pane>
    </el-tabs>

    <el-divider />
    <p class="disclaimer">{{ plan.meta.disclaimer }}</p>
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PlanJson } from '@/types/plan'

const props = defineProps<{ plan: PlanJson }>()
const activeTab = ref('overview')

const riskLabel = computed(() => {
  const map: Record<string, string> = { low: '低', medium: '中', high: '高' }
  return map[props.plan.risk.level] ?? props.plan.risk.level
})
</script>

<style scoped>
.plan-card {
  margin-top: 16px;
}
.meals {
  margin: 8px 0;
}
.notes {
  margin-top: 8px;
}
.disclaimer {
  color: #909399;
  font-size: 12px;
  text-align: center;
}
</style>
