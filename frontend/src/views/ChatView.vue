<template>
  <div class="chat-page">
    <div class="chat-header">🧘 健康计划生成助手</div>

    <div ref="messagesRef" class="chat-messages">
      <div v-if="messages.length === 0" class="welcome">
        <p>👋 你好！告诉我你的身体信息，我来帮你生成健康计划。</p>
        <p class="hint">示例：我男28岁175cm82kg想减脂</p>
        <p class="hint">生成后可以继续追问，比如「把运动强度调低」</p>
      </div>

      <div v-for="(m, i) in messages" :key="i" :class="['msg-row', m.role]">
        <div v-if="m.role === 'user'" class="bubble user">{{ m.text }}</div>
        <div v-else class="assistant-body">
          <RiskBanner v-if="m.plan" :risk="m.plan.risk" />
          <PlanCard v-if="m.plan" :plan="m.plan" />
          <div v-else class="bubble assistant">{{ m.text }}</div>
        </div>
      </div>

      <div v-if="loading" class="msg-row assistant">
        <div class="bubble assistant typing">{{ stepText }}</div>
      </div>
    </div>

    <div class="chat-input">
      <el-input
        v-model="input"
        placeholder="输入身体信息或调整需求，回车发送"
        :disabled="loading"
        @keyup.enter="send"
      />
      <el-button type="primary" :loading="loading" @click="send">发送</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import PlanCard from '@/components/PlanCard.vue'
import RiskBanner from '@/components/RiskBanner.vue'
import { streamChat } from '@/api/sse'
import type { ChatMessage } from '@/types/plan'

const input = ref('')
const loading = ref(false)
const messages = ref<ChatMessage[]>([])
const stepText = ref('🤖 思考中')
const messagesRef = ref<HTMLElement>()
const sessionId = crypto.randomUUID()
const userId = 'default'

function scrollToBottom() {
  nextTick(() => {
    messagesRef.value?.scrollTo({ top: messagesRef.value.scrollHeight, behavior: 'smooth' })
  })
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  input.value = ''
  messages.value.push({ role: 'user', text })
  loading.value = true
  stepText.value = '🤖 思考中'
  try {
    await streamChat(text, sessionId, userId, {
      onIntent: (intent) => {
        stepText.value = intent === 'generate' ? '🧭 首次生成' : '🧭 追问调整'
      },
      onStep: (e) => {
        stepText.value = `${e.emoji} ${e.name} 中...`
      },
      onDone: (plan) => {
        messages.value.push({ role: 'assistant', text: '', plan })
      },
      onError: (msg) => {
        messages.value.push({ role: 'assistant', text: `⚠️ 出错了：${msg}` })
      },
    })
  } catch (e) {
    messages.value.push({
      role: 'assistant',
      text: `⚠️ ${e instanceof Error ? e.message : String(e)}`,
    })
  } finally {
    loading.value = false
    stepText.value = '🤖 思考中'
    scrollToBottom()
  }
}
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 900px;
  margin: 0 auto;
}

.chat-header {
  padding: 16px 20px;
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  border-bottom: 1px solid #ebeef5;
  background: #fff;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: #f5f7fa;
}

.welcome {
  text-align: center;
  color: #606266;
  margin-top: 60px;
}

.welcome .hint {
  color: #909399;
  font-size: 13px;
  margin-top: 8px;
}

.msg-row {
  display: flex;
  margin-bottom: 16px;
}

.msg-row.user {
  justify-content: flex-end;
}

.assistant-body {
  max-width: 90%;
}

.bubble {
  padding: 10px 14px;
  border-radius: 10px;
  line-height: 1.6;
  word-break: break-word;
}

.bubble.user {
  background: #409eff;
  color: #fff;
  max-width: 70%;
}

.bubble.assistant {
  background: #fff;
  color: #303133;
  border: 1px solid #ebeef5;
}

.bubble.typing {
  color: #909399;
  font-size: 13px;
}

.chat-input {
  display: flex;
  gap: 10px;
  padding: 14px 20px;
  background: #fff;
  border-top: 1px solid #ebeef5;
}
</style>
