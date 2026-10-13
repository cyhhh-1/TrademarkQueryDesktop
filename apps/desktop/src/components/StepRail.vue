<script setup lang="ts">
defineProps<{ current: number }>()

const steps = ['选择表格', '登录网站', '自动查询', '保存结果']
</script>

<template>
  <nav class="step-rail" aria-label="查询步骤">
    <div
      v-for="(step, index) in steps"
      :key="step"
      class="step"
      :class="{ active: current === index + 1, complete: current > index + 1 }"
    >
      <div class="step-line step-line-before" aria-hidden="true" />
      <span class="step-number">{{ index + 1 }}</span>
      <div class="step-line step-line-after" aria-hidden="true" />
      <span class="step-label">{{ step }}</span>
    </div>
  </nav>
</template>
