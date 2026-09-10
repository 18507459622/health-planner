<template>
  <el-card>
    <template #header>🏥 健康信息</template>
    <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
      <el-form-item label="性别" prop="gender">
        <el-radio-group v-model="form.gender">
          <el-radio value="男">男</el-radio>
          <el-radio value="女">女</el-radio>
        </el-radio-group>
      </el-form-item>

      <el-form-item label="年龄" prop="age">
        <el-input-number v-model="form.age" :min="1" :max="120" />
      </el-form-item>

      <el-form-item label="身高(cm)" prop="height_cm">
        <el-input-number v-model="form.height_cm" :min="100" :max="250" :step="1" />
      </el-form-item>

      <el-form-item label="体重(kg)" prop="weight_kg">
        <el-input-number v-model="form.weight_kg" :min="30" :max="300" :step="0.5" :precision="1" />
      </el-form-item>

      <el-form-item label="健康目标" prop="goal">
        <el-radio-group v-model="form.goal">
          <el-radio-button value="减脂">减脂</el-radio-button>
          <el-radio-button value="增肌">增肌</el-radio-button>
          <el-radio-button value="养生">养生</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <el-form-item label="基础疾病" prop="diseases">
        <el-select v-model="form.diseases" multiple placeholder="无" clearable style="width: 100%">
          <el-option v-for="d in diseaseOptions" :key="d" :label="d" :value="d" />
        </el-select>
      </el-form-item>

      <el-form-item label="所在城市" prop="city">
        <el-input v-model="form.city" placeholder="北京" />
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="onSubmit">
          {{ loading ? '生成中...' : '生成健康计划' }}
        </el-button>
      </el-form-item>
    </el-form>

    <el-divider v-if="weightHistory.length" />
    <div v-if="weightHistory.length">
      <div class="wh-title">📈 体重历史</div>
      <el-timeline>
        <el-timeline-item
          v-for="(w, i) in [...weightHistory].reverse()"
          :key="i"
          :timestamp="w.date"
        >
          {{ w.weight }} kg
        </el-timeline-item>
      </el-timeline>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import type { ProfileInput, StoredProfile, WeightEntry } from '@/types/plan'

const props = withDefaults(
  defineProps<{
    loading: boolean
    initialProfile?: StoredProfile | null
    weightHistory?: WeightEntry[]
  }>(),
  {
    weightHistory: () => [],
  },
)
const emit = defineEmits<{ (e: 'submit', payload: ProfileInput): void }>()

const formRef = ref<FormInstance>()
const diseaseOptions = ['高血压', '糖尿病', '心脏病', '冠心病', '哮喘', '肾病', '痛风', '甲状腺']

const form = reactive<ProfileInput>({
  gender: '男',
  age: 28,
  height_cm: 175,
  weight_kg: 82,
  goal: '减脂',
  diseases: [],
  city: '北京',
})

// 预填长期画像
watch(
  () => props.initialProfile,
  (p) => {
    if (!p) return
    form.gender = p.gender || '男'
    form.age = p.age ?? 28
    form.height_cm = p.height_cm ?? 175
    form.weight_kg = p.weight_kg ?? 82
    form.goal = p.goal || '减脂'
    form.diseases = p.diseases || []
    form.city = p.city || '北京'
  },
  { immediate: true },
)

const rules: FormRules = {
  age: [{ required: true, message: '请输入年龄', trigger: 'blur' }],
  height_cm: [{ required: true, message: '请输入身高', trigger: 'blur' }],
  weight_kg: [{ required: true, message: '请输入体重', trigger: 'blur' }],
}

function onSubmit() {
  formRef.value?.validate((valid) => {
    if (valid) emit('submit', { ...form, diseases: [...form.diseases] })
  })
}
</script>

<style scoped>
.wh-title {
  font-weight: 600;
  margin-bottom: 8px;
}
</style>
