import type { PlanJson } from '@/types/plan'

const RISK_LABEL: Record<string, string> = { low: '低', medium: '中', high: '高' }

function riskLabel(level: string): string {
  return RISK_LABEL[level] ?? level
}

function join(arr: string[] | undefined, sep: string): string {
  return (arr || []).join(sep) || '无'
}

/** 计划 → Markdown 文本。 */
export function planToMarkdown(plan: PlanJson): string {
  const lines: string[] = []
  lines.push(`# 健康计划：${plan.meta.goal}`)
  lines.push('')
  if (plan.meta.generated_at) lines.push(`> 生成时间：${plan.meta.generated_at}`)
  lines.push('')
  lines.push('## 用户画像')
  lines.push(`- 性别：${plan.profile.gender}`)
  lines.push(`- 年龄：${plan.profile.age} 岁`)
  lines.push(`- 身高：${plan.profile.height_cm} cm`)
  lines.push(`- 体重：${plan.profile.weight_kg} kg`)
  if (plan.profile.bmi != null) lines.push(`- BMI：${plan.profile.bmi}（${plan.profile.bmi_category}）`)
  lines.push('')
  lines.push('## 风险提示')
  lines.push(`- 风险等级：${riskLabel(plan.risk.level)}`)
  lines.push(`- 是否建议就医：${plan.risk.need_medical ? '是' : '否'}`)
  if ((plan.risk.notes || []).length) lines.push(`- 说明：${join(plan.risk.notes, '；')}`)
  lines.push('')
  lines.push('## 饮食计划')
  lines.push(`- 目标热量：${plan.diet.target_calories_kcal} kcal`)
  lines.push(
    `- 宏量配比：蛋白质 ${plan.diet.macro.protein_g}g / 碳水 ${plan.diet.macro.carb_g}g / 脂肪 ${plan.diet.macro.fat_g}g`,
  )
  if ((plan.diet.principles || []).length) lines.push(`- 原则：${join(plan.diet.principles, '；')}`)
  lines.push('')
  lines.push('### 三餐示例')
  lines.push(`- 早餐：${plan.diet.sample_meals?.breakfast}`)
  lines.push(`- 午餐：${plan.diet.sample_meals?.lunch}`)
  lines.push(`- 晚餐：${plan.diet.sample_meals?.dinner}`)
  if ((plan.diet.avoid || []).length) lines.push(`- 避免：${join(plan.diet.avoid, '、')}`)
  lines.push('')
  lines.push('## 运动计划')
  lines.push(`- 每周频率：${plan.exercise.weekly_frequency}`)
  if ((plan.exercise.weekly_plan || []).length) {
    lines.push('')
    lines.push('| 星期 | 类型 | 时长(分钟) | 强度 |')
    lines.push('| --- | --- | --- | --- |')
    for (const item of plan.exercise.weekly_plan) {
      lines.push(`| ${item.day} | ${item.type} | ${item.duration_min} | ${item.intensity} |`)
    }
  }
  if ((plan.exercise.notes || []).length) {
    lines.push('')
    lines.push(`- 注意：${join(plan.exercise.notes, '；')}`)
  }
  lines.push('')
  lines.push('## 作息计划')
  lines.push(
    `- 睡眠：${plan.lifestyle.sleep?.target_hours} 小时（${plan.lifestyle.sleep?.sleep_time} - ${plan.lifestyle.sleep?.wake_time}）`,
  )
  if ((plan.lifestyle.routine || []).length) lines.push(`- 日常节律：${join(plan.lifestyle.routine, '；')}`)
  if ((plan.lifestyle.stress_management || []).length)
    lines.push(`- 压力管理：${join(plan.lifestyle.stress_management, '；')}`)
  lines.push('')
  lines.push('---')
  lines.push(plan.meta.disclaimer)
  return lines.join('\n')
}

/** 计划 → 纯文本（去 Markdown 语法）。 */
export function planToText(plan: PlanJson): string {
  return planToMarkdown(plan)
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/^\|\s*/gm, '')
    .replace(/^\|\s*$/gm, '')
    .replace(/^>\s*/gm, '')
    .replace(/^\*\s*/gm, '· ')
    .replace(/^---$/gm, '----------------------------------------')
    .replace(/^|$/gm, '')
}

/** 触发浏览器下载。 */
export function downloadFile(filename: string, content: string, mimeType = 'text/plain'): void {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
