// 与后端结构化 JSON 计划 schema 对应的 TS 接口

export interface ProfileInput {
  gender: string
  age: number
  height_cm: number
  weight_kg: number
  goal: string
  diseases: string[]
  city: string
}

export interface PlanMeta {
  goal: string
  generated_at: string
  disclaimer: string
}

export interface PlanProfile {
  gender: string
  age: number
  height_cm: number
  weight_kg: number
  bmi: number | null
  bmi_category: string
}

export interface PlanRisk {
  level: 'low' | 'medium' | 'high'
  need_medical: boolean
  notes: string[]
}

export interface Macro {
  protein_g: number
  carb_g: number
  fat_g: number
}

export interface Diet {
  target_calories_kcal: number
  macro: Macro
  principles: string[]
  sample_meals: { breakfast: string; lunch: string; dinner: string }
  avoid: string[]
}

export interface ExerciseItem {
  day: string
  type: string
  duration_min: number
  intensity: string
}

export interface Exercise {
  weekly_frequency: number
  weekly_plan: ExerciseItem[]
  notes: string[]
}

export interface Lifestyle {
  sleep: { target_hours: number; sleep_time: string; wake_time: string }
  routine: string[]
  stress_management: string[]
}

export interface PlanJson {
  meta: PlanMeta
  profile: PlanProfile
  risk: PlanRisk
  diet: Diet
  exercise: Exercise
  lifestyle: Lifestyle
}

export interface PlanEvent {
  node: string
  emoji: string
  name: string
  content: string
  final: boolean
}

// ---------- 人机协同（Human-in-the-loop）中断相关 ----------
// 图执行到闸门（风险确认 / 计划确认）时会暂停，SSE 流里出现一帧中断事件，
// 前端渲染成确认卡片，用户做出选择后再调 /api/chat/resume 恢复执行。

export interface InterruptOption {
  value: string
  label: string
  /** 按钮语义，直接映射到 Element Plus 的按钮 type；后端漏传时按次要按钮处理 */
  style?: 'primary' | 'default' | 'danger'
}

export interface InterruptPayload {
  /** risk_confirmation（需就医确认） | plan_confirmation（计划确认） */
  type: string
  title: string
  question: string
  options: InterruptOption[]
  hint?: string
  /** 仅 risk_confirmation：风险等级，画像算不出来时后端给 unknown */
  risk_level?: string
  /** 仅 risk_confirmation：BMI，画像缺失时为 null（此时不展示） */
  bmi?: number | null
  /** 仅 plan_confirmation：已调整轮数 */
  revision_count?: number
  /** 仅 plan_confirmation：允许调整的轮数上限（防用户无限要求调整） */
  max_revisions?: number
}

/** 中断帧：收到它代表流程已暂停，必须由用户确认后才能继续 */
export interface InterruptEvent {
  type: 'interrupt'
  thread_id: string
  interrupt: InterruptPayload
  trace_id?: string
}

/** 首帧追踪事件：把前端现象跟后端日志对齐时要用它 */
export interface TraceEvent {
  type: 'trace'
  trace_id: string
}

/** 用户在确认卡片上做出的决定，也就是 /api/chat/resume 的请求体内容 */
export interface ConfirmDecision {
  decision: string
  /** 只在「需要调整」时有值，其余情况为空串 */
  feedback: string
}

/** 风险闸门选择中止时后端给出的「就医建议」，没有 diet / exercise 等计划字段 */
export interface AbortedPlan {
  meta: { aborted: boolean; abort_reason: string }
  summary: string
  profile: { bmi: number | null }
  risk: { level: string; need_medical: boolean }
  next_steps: string[]
  assessment: string
}

/** 最终帧（final: true）的两种形态：完整计划，或用户中止后的就医建议 */
export type FinalPayload = PlanJson | AbortedPlan

export interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  plan?: PlanJson | null
}

export interface StoredProfile {
  gender: string
  age: number | null
  height_cm: number | null
  weight_kg: number | null
  goal: string
  diseases: string[]
  city: string
}

export interface WeightEntry {
  weight: number
  date: string
}

export interface MemoryResponse {
  profile: StoredProfile | null
  weight_history: WeightEntry[]
}
