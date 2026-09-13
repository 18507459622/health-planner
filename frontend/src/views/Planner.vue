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
        <ConfirmCard
          v-if="interruptPayload"
          :payload="interruptPayload"
          @confirm="handleConfirm"
        />
        <RiskBanner v-if="plan" :risk="plan.risk" />
        <PlanCard v-if="plan" :plan="plan" />

        <el-card v-if="aborted" class="mt">
          <template #header>🛑 已中止生成</template>
          <p class="abort-summary">{{ aborted.summary }}</p>
          <ul class="abort-steps">
            <li v-for="(s, i) in aborted.next_steps || []" :key="i">{{ s }}</li>
          </ul>
          <p v-if="aborted.meta.abort_reason" class="abort-reason">{{ aborted.meta.abort_reason }}</p>
        </el-card>
        <el-alert
          v-else-if="abortNotice"
          :title="abortNotice"
          type="warning"
          show-icon
          :closable="false"
          class="mt"
        />

        <el-card v-if="plan" class="mt">
          <div class="adjust-row">
            <el-input
              v-model="instruction"
              placeholder="调整计划，如：把运动强度调低"
              :disabled="loading || !!interruptPayload"
              @keyup.enter="handleAdjust"
            />
            <el-button
              type="primary"
              :loading="loading"
              :disabled="!!interruptPayload"
              @click="handleAdjust"
            >
              调整
            </el-button>
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
import ConfirmCard from '@/components/ConfirmCard.vue'
import { resumeChat, streamChat } from '@/api/sse'
import type { StreamHandlers } from '@/api/sse'
import { downloadFile, planToMarkdown, planToText } from '@/utils/export'
import type {
  AbortedPlan,
  ConfirmDecision,
  FinalPayload,
  InterruptPayload,
  PlanJson,
  ProfileInput,
  StoredProfile,
  WeightEntry,
} from '@/types/plan'

const loading = ref(false)
const error = ref('')
const plan = ref<PlanJson | null>(null)
const stage = ref(0)
const instruction = ref('')
const profile = ref<StoredProfile | null>(null)
const weightHistory = ref<WeightEntry[]>([])
// 人机协同：闸门暂停时暂存确认载荷和线程号，用户确认后靠它们恢复执行
const interruptPayload = ref<InterruptPayload | null>(null)
const threadId = ref('')
const traceId = ref('')
// 风险闸门选「中止」时后端给的是就医建议，没有计划字段，所以单独存
const aborted = ref<AbortedPlan | null>(null)
const abortNotice = ref('')

const sessionId = crypto.randomUUID()
const userId = 'default'

// 已完成的节点跨「恢复」保留：同一轮对话里进度只能往前追加，不能因为中断而回退
let completed = new Set<string>()

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

/** 中止时后端返回的是就医建议对象（meta.aborted），字段结构与完整计划不同。 */
function isAborted(p: FinalPayload): p is AbortedPlan {
  return (p.meta as { aborted?: boolean }).aborted === true
}

/** 首轮流与恢复流共用同一套回调，这样恢复后的节点进度才能接着往上累加。 */
function streamHandlers(): StreamHandlers {
  return {
    onTrace: (id) => {
      traceId.value = id
    },
    onIntent: (intent) => {
      if (intent === 'adjust') stage.value = 3
    },
    onStep: (e) => {
      completed.add(e.node)
      stage.value = stageOf(completed)
    },
    onDone: (p) => {
      abortNotice.value = ''
      if (isAborted(p)) {
        // 中止分支不产出计划，PlanCard 会因缺 diet/exercise 字段渲染报错，所以分开存
        aborted.value = p
        plan.value = null
      } else {
        plan.value = p
        aborted.value = null
        stage.value = 4
      }
    },
    onInterrupt: (payload, tid) => {
      interruptPayload.value = payload
      threadId.value = tid
      // 图已经暂停，再转圈会让用户误以为还在生成
      loading.value = false
    },
    onError: (msg) => {
      // 中断相关问题要拿 trace_id 去对齐后端日志，所以直接带到错误信息里
      error.value = traceId.value ? `${msg}（trace_id: ${traceId.value}）` : msg
    },
  }
}

async function runFlow(message: string) {
  loading.value = true
  error.value = ''
  stage.value = 0
  completed = new Set<string>()
  interruptPayload.value = null // 新一轮对话作废上一轮遗留的确认卡片
  aborted.value = null
  abortNotice.value = ''
  try {
    await streamChat(message, sessionId, userId, streamHandlers())
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

/** 用户在确认卡片上做了选择：带上 thread_id 恢复被闸门暂停的图。 */
async function handleConfirm(decision: ConfirmDecision) {
  const tid = threadId.value
  interruptPayload.value = null
  if (!tid) {
    // 没有 thread_id 就恢复不了，明确报错好过静默卡住
    error.value = '缺少 thread_id，无法继续本次执行，请重新生成计划。'
    return
  }
  if (decision.decision === 'abort') {
    abortNotice.value = '已按你的选择中止，本次不会生成健康计划；建议先就医评估。'
  }
  loading.value = true
  error.value = ''
  try {
    // 这里刻意不重置 stage / completed：恢复后进度要在现有基础上继续往下走
    await resumeChat(tid, decision.decision, decision.feedback, streamHandlers())
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
  if (!text || loading.value || interruptPayload.value) return
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
.abort-summary {
  margin: 0 0 8px;
}
.abort-steps {
  margin: 0;
  padding-left: 20px;
  color: #606266;
  line-height: 1.8;
}
.abort-reason {
  margin: 12px 0 0;
  color: #909399;
  font-size: 12px;
}
</style>
