<template>
  <div class="page">
    <h1 class="page-title">🧘 健康计划生成助手</h1>
    <el-row :gutter="24">
      <el-col :span="8">
        <ProfileForm
          :loading="loading"
          :initial-profile="profile"
          :weight-history="weightHistory"
          @submit="handleGenerate"
        />
      </el-col>
      <el-col :span="16">
        <ProgressSteps :stage="stage" />
        <el-alert v-if="error" :title="error" type="error" show-icon class="mt" :closable="false" />
        <RiskBanner v-if="plan" :risk="plan.risk" />
        <PlanCard v-if="plan" :plan="plan" />

        <el-card v-if="plan" class="mt">
          <div class="adjust-row">
            <el-input
              v-model="instruction"
              placeholder="调整计划，如：把运动强度调低"
              :disabled="loading"
              @keyup.enter="handleAdjust"
            />
            <el-button type="primary" :loading="loading" @click="handleAdjust">调整</el-button>
          </div>
          <el-divider />
          <div class="export-row">
            <span class="export-label">导出计划：</span>
            <el-button size="small" @click="exportMd">Markdown (.md)</el-button>
            <el-button size="small" @click="exportTxt">纯文本 (.txt)</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ProfileForm from '@/components/ProfileForm.vue'
import ProgressSteps from '@/components/ProgressSteps.vue'
import RiskBanner from '@/components/RiskBanner.vue'
import PlanCard from '@/components/PlanCard.vue'
import { streamChat } from '@/api/sse'
import { downloadFile, planToMarkdown, planToText } from '@/utils/export'
import type { PlanJson, ProfileInput, StoredProfile, WeightEntry } from '@/types/plan'

const loading = ref(false)
const error = ref('')
const plan = ref<PlanJson | null>(null)
const stage = ref(0)
const instruction = ref('')
const profile = ref<StoredProfile | null>(null)
const weightHistory = ref<WeightEntry[]>([])

const sessionId = crypto.randomUUID()
const userId = 'default'

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

function formatProfileMessage(p: ProfileInput): string {
  const diseases = p.diseases.length ? p.diseases.join('、') : '无'
  return `我${p.gender}，${p.age}岁，身高${p.height_cm}cm，体重${p.weight_kg}kg，想${p.goal}，基础疾病：${diseases}，在${p.city}。`
}

async function loadMemory() {
  try {
    const resp = await fetch(`/api/memory/${userId}`)
    const data = await resp.json()
    profile.value = data.profile
    weightHistory.value = data.weight_history || []
  } catch {
    // 首次使用无记忆，忽略
  }
}

async function runFlow(message: string) {
  loading.value = true
  error.value = ''
  stage.value = 0
  const completed = new Set<string>()
  try {
    await streamChat(message, sessionId, userId, {
      onIntent: (intent) => {
        if (intent === 'adjust') stage.value = 3
      },
      onStep: (e) => {
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

async function handleGenerate(payload: ProfileInput) {
  await runFlow(formatProfileMessage(payload))
  loadMemory() // 刷新画像 + 体重历史
}

function handleAdjust() {
  const text = instruction.value.trim()
  if (!text || loading.value) return
  instruction.value = ''
  runFlow(text)
}

function exportMd() {
  if (plan.value) {
    downloadFile(`健康计划-${plan.value.meta.goal}.md`, planToMarkdown(plan.value), 'text/markdown')
  }
}

function exportTxt() {
  if (plan.value) {
    downloadFile(`健康计划-${plan.value.meta.goal}.txt`, planToText(plan.value), 'text/plain')
  }
}

onMounted(loadMemory)
</script>

<style scoped>
.mt {
  margin-top: 16px;
}
.adjust-row {
  display: flex;
  gap: 10px;
}
.adjust-row .el-input {
  flex: 1;
}
.export-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.export-label {
  color: #606266;
  font-size: 13px;
}
</style>
