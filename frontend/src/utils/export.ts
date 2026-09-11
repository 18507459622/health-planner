import html2pdf from 'html2pdf.js'

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

/** 计划 → 排版后的 HTML 报告（供 PDF 导出）。 */
function planToHtml(plan: PlanJson): string {
  const rows = (plan.exercise.weekly_plan || [])
    .map(
      (i) =>
        `<tr><td style="border:1px solid #ccc;padding:4px;">${i.day}</td>` +
        `<td style="border:1px solid #ccc;padding:4px;">${i.type}</td>` +
        `<td style="border:1px solid #ccc;padding:4px;text-align:center;">${i.duration_min}</td>` +
        `<td style="border:1px solid #ccc;padding:4px;text-align:center;">${i.intensity}</td></tr>`,
    )
    .join('')

  return `
<div style="font-family:'Microsoft YaHei','PingFang SC',sans-serif;color:#333;line-height:1.8;">
  <h1 style="text-align:center;font-size:22px;margin-bottom:4px;">健康计划：${plan.meta.goal}</h1>
  <p style="text-align:center;color:#999;font-size:12px;">${plan.meta.generated_at || ''}</p>

  <h2 style="border-bottom:2px solid #409eff;padding-bottom:4px;">用户画像</h2>
  <ul style="margin:6px 0;">
    <li>性别：${plan.profile.gender}　年龄：${plan.profile.age} 岁</li>
    <li>身高：${plan.profile.height_cm} cm　体重：${plan.profile.weight_kg} kg</li>
    ${plan.profile.bmi != null ? `<li>BMI：${plan.profile.bmi}（${plan.profile.bmi_category}）</li>` : ''}
  </ul>

  <h2 style="border-bottom:2px solid #409eff;padding-bottom:4px;">风险提示</h2>
  <ul style="margin:6px 0;">
    <li>风险等级：${riskLabel(plan.risk.level)}　建议就医：${plan.risk.need_medical ? '是' : '否'}</li>
    ${(plan.risk.notes || []).map((n) => `<li>${n}</li>`).join('')}
  </ul>

  <h2 style="border-bottom:2px solid #409eff;padding-bottom:4px;">饮食计划</h2>
  <ul style="margin:6px 0;">
    <li>目标热量：${plan.diet.target_calories_kcal} kcal</li>
    <li>宏量配比：蛋白质 ${plan.diet.macro.protein_g}g / 碳水 ${plan.diet.macro.carb_g}g / 脂肪 ${plan.diet.macro.fat_g}g</li>
    <li>原则：${join(plan.diet.principles, '；')}</li>
  </ul>
  <p><strong>三餐示例：</strong></p>
  <ul style="margin:6px 0;">
    <li>早餐：${plan.diet.sample_meals?.breakfast}</li>
    <li>午餐：${plan.diet.sample_meals?.lunch}</li>
    <li>晚餐：${plan.diet.sample_meals?.dinner}</li>
  </ul>
  <p><strong>避免：</strong>${join(plan.diet.avoid, '、')}</p>

  <h2 style="border-bottom:2px solid #409eff;padding-bottom:4px;">运动计划</h2>
  <p>每周频率：${plan.exercise.weekly_frequency}</p>
  <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <tr style="background:#f0f5ff;">
      <th style="border:1px solid #ccc;padding:4px;">星期</th>
      <th style="border:1px solid #ccc;padding:4px;">类型</th>
      <th style="border:1px solid #ccc;padding:4px;">时长(分)</th>
      <th style="border:1px solid #ccc;padding:4px;">强度</th>
    </tr>
    ${rows}
  </table>
  <p><strong>注意：</strong>${join(plan.exercise.notes, '；')}</p>

  <h2 style="border-bottom:2px solid #409eff;padding-bottom:4px;">作息计划</h2>
  <ul style="margin:6px 0;">
    <li>睡眠：${plan.lifestyle.sleep?.target_hours} 小时（${plan.lifestyle.sleep?.sleep_time} - ${plan.lifestyle.sleep?.wake_time}）</li>
    <li>日常节律：${join(plan.lifestyle.routine, '；')}</li>
    <li>压力管理：${join(plan.lifestyle.stress_management, '；')}</li>
  </ul>

  <hr style="border:none;border-top:1px solid #ddd;margin:16px 0;" />
  <p style="font-size:11px;color:#999;">${plan.meta.disclaimer}</p>
</div>`
}

/** 导出 PDF（html2pdf.js：html2canvas 渲染 → jsPDF 生成）。 */
export async function exportPlanPdf(plan: PlanJson): Promise<void> {
  const container = document.createElement('div')
  container.innerHTML = planToHtml(plan)
  container.style.cssText = 'position:absolute;left:-9999px;top:0;width:760px;background:#fff;'
  document.body.appendChild(container)
  try {
    await html2pdf()
      .set({
        margin: [12, 12, 12, 12],
        filename: `健康计划-${plan.meta.goal}.pdf`,
        image: { type: 'jpeg', quality: 0.98 },
        html2canvas: { scale: 2, useCORS: true, backgroundColor: '#fff' },
        jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
      })
      .from(container)
      .save()
  } finally {
    document.body.removeChild(container)
  }
}
