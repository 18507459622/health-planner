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
