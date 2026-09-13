<template>
  <el-card class="confirm-card" :class="{ 'is-risk': isRisk }" shadow="never">
    <div class="confirm-head">
      <span class="confirm-emoji">{{ emoji }}</span>
      <div>
        <div class="confirm-title">{{ payload.title }}</div>
        <div class="confirm-question">{{ payload.question }}</div>
      </div>
    </div>

    <div v-if="facts.length" class="confirm-facts">
      <el-tag v-for="f in facts" :key="f.label" :type="f.type" size="small" effect="light">
        {{ f.label }}
      </el-tag>
    </div>

    <div class="confirm-actions">
      <el-button
        v-for="opt in payload.options"
        :key="opt.value"
        :type="buttonType(opt.style)"
        @click="choose(opt)"
      >
        {{ opt.label }}
      </el-button>
    </div>

    <template v-if="revising">
      <el-input
        v-model="feedback"
        type="textarea"
        :rows="3"
        maxlength="200"
        show-word-limit
        class="confirm-input"
        placeholder="想改哪里？例如：运动强度调低一点、晚餐再清淡些（可留空，直接提交）"
      />
      <div class="confirm-submit">
        <el-button type="primary" @click="submitRevise">提交调整意见</el-button>
        <el-button @click="cancelRevise">取消</el-button>
      </div>
    </template>

    <p v-if="payload.hint" class="confirm-hint">{{ payload.hint }}</p>
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ConfirmDecision, InterruptOption, InterruptPayload } from '@/types/plan'

const props = defineProps<{ payload: InterruptPayload }>()
const emit = defineEmits<{ (e: 'confirm', payload: ConfirmDecision): void }>()

/** 「需要调整」这个动作要先收集意见，所以单独识别出来走两步交互 */
const REVISE_VALUE = 'revise'

const RISK_TEXT: Record<string, string> = { low: '低', medium: '中', high: '高', unknown: '未知' }

const revising = ref(false)
const feedback = ref('')

const isRisk = computed(() => props.payload.type === 'risk_confirmation')

const emoji = computed(() => {
  if (props.payload.type === 'risk_confirmation') return '⚠️'
  if (props.payload.type === 'plan_confirmation') return '📝'
  return '❓'
})

function riskTagType(level: string): 'danger' | 'warning' | 'success' | 'info' {
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  if (level === 'low') return 'success'
  return 'info'
}

/** 卡片顶部的事实标签：只在载荷确实带了这个字段时才显示，避免出现 "BMI：null" */
const facts = computed(() => {
  const p = props.payload
  const list: { label: string; type: 'danger' | 'warning' | 'success' | 'info' }[] = []

  if (p.type === 'risk_confirmation') {
    if (p.risk_level) {
      const text = RISK_TEXT[p.risk_level] ?? p.risk_level
      list.push({ label: `风险等级：${text}`, type: riskTagType(p.risk_level) })
    }
    if (p.bmi != null) {
      list.push({ label: `BMI：${p.bmi}`, type: 'info' })
    }
  }
  if (p.type === 'plan_confirmation') {
    list.push({
      label: `已调整 ${p.revision_count ?? 0} / ${p.max_revisions ?? 0} 轮`,
      type: 'info',
    })
  }
  return list
})

/** 后端只承诺 primary / default / danger 三种，出现别的值按次要按钮兜底 */
function buttonType(style?: string): 'primary' | 'default' | 'danger' {
  if (style === 'primary' || style === 'danger') return style
  return 'default'
}

function choose(opt: InterruptOption) {
  if (opt.value === REVISE_VALUE) {
    revising.value = true
    return
  }
  // 其余选项点下去就代表决定已定，无需二次确认，直接上报
  cancelRevise()
  emit('confirm', { decision: opt.value, feedback: '' })
}

function submitRevise() {
  const text = feedback.value.trim()
  revising.value = false
  feedback.value = ''
  emit('confirm', { decision: REVISE_VALUE, feedback: text })
}

function cancelRevise() {
  revising.value = false
  feedback.value = ''
}
</script>

<style scoped>
/* 左侧彩色竖条 + 浅色底：一眼能看出「这里卡住等我做决定」 */
.confirm-card {
  margin-top: 16px;
  border-left: 4px solid var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.confirm-card.is-risk {
  border-left-color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.confirm-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.confirm-emoji {
  font-size: 22px;
  line-height: 1.2;
}
.confirm-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
.confirm-question {
  margin-top: 6px;
  color: #606266;
}
.confirm-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.confirm-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}
.confirm-input {
  margin-top: 12px;
}
.confirm-submit {
  display: flex;
  gap: 10px;
  margin-top: 10px;
}
.confirm-hint {
  margin: 12px 0 0;
  color: #909399;
  font-size: 12px;
}
</style>
