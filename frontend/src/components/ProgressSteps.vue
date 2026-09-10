<template>
  <el-card>
    <el-steps :active="stage" finish-status="success" align-center>
      <el-step title="画像解析" description="整理身体指标" />
      <el-step title="健康评估" description="BMI + 风险把关" />
      <el-step title="并行规划" description="膳食/运动/作息" />
      <el-step title="计划合成" description="生成 JSON" />
    </el-steps>
  </el-card>
</template>

<script setup lang="ts">
defineProps<{ stage: number }>()
</script>
