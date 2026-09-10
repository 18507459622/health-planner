<template>
  <el-alert
    v-if="showError"
    :title="title"
    type="error"
    :description="description"
    show-icon
    :closable="false"
    class="banner"
  />
  <el-alert
    v-else-if="showWarning"
    :title="title"
    type="warning"
    :description="description"
    show-icon
    :closable="false"
    class="banner"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PlanRisk } from '@/types/plan'

const props = defineProps<{ risk: PlanRisk }>()

const showError = computed(() => props.risk.level === 'high' || props.risk.need_medical)
const showWarning = computed(() => props.risk.level === 'medium')

const title = computed(() => (props.risk.need_medical ? '⚠️ 建议尽快就医咨询' : '健康风险提示'))
const description = computed(() => (props.risk.notes || []).join('；'))
</script>

<style scoped>
.banner {
  margin-top: 16px;
}
</style>
