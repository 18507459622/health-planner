<template>
  <div class="page">
    <h1 class="page-title">🧘 健康计划生成助手</h1>
    <el-row :gutter="24">
      <el-col :span="8">
        <ProfileForm :loading="loading" @submit="handleSubmit" />
      </el-col>
      <el-col :span="16">
        <ProgressSteps :stage="stage" />
        <el-alert v-if="error" :title="error" type="error" show-icon class="mt" :closable="false" />
        <el-collapse v-if="stepLog.length" v-model="openLogs" class="mt">
          <el-collapse-item v-for="s in stepLog" :key="s.node" :name="s.node">
            <template #title>{{ s.emoji }} {{ s.name }}</template>
            <pre class="raw">{{ s.content }}</pre>
          </el-collapse-item>
        </el-collapse>
        <RiskBanner v-if="plan" :risk="plan.risk" />
        <PlanCard v-if="plan" :plan="plan" />
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import ProfileForm from '@/components/ProfileForm.vue'
import ProgressSteps from '@/components/ProgressSteps.vue'
import RiskBanner from '@/components/RiskBanner.vue'
import PlanCard from '@/components/PlanCard.vue'
import { streamPlan } from '@/api/sse'
import type { PlanEvent, PlanJson, ProfileInput } from '@/types/plan'

const loading = ref(false)
const error = ref('')
const plan = ref<PlanJson | null>(null)
const stepLog = ref<PlanEvent[]>([])
const openLogs = ref<string[]>([])
const stage = ref(0)

function stageOf(completed: Set<string>): number {
  if (completed.has('plan_composer')) return 4
  if (
    completed.has('diet_planner') &&
    completed.has('exercise_planner') &&
    completed.has('lifestyle_planner')
  )
    return 3
  if (completed.has('health_assessor')) return 2
  if (completed.has('profile_parser')) return 1
  return 0
}

async function handleSubmit(payload: ProfileInput) {
  loading.value = true
  error.value = ''
  plan.value = null
  stepLog.value = []
  openLogs.value = []
  stage.value = 0
  const completed = new Set<string>()
  try {
    await streamPlan(payload, {
      onStep: (e) => {
        stepLog.value.push(e)
        completed.add(e.node)
        stage.value = stageOf(completed)
      },
      onDone: (p) => {
        plan.value = p
        stage.value = 4
      },
      onError: (msg) => {
        error.value = msg
      },
    })
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.mt {
  margin-top: 16px;
}
</style>
