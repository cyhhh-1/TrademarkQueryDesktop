<script setup lang="ts">
import type { TaskCounts } from '../domain/types'

defineProps<{ counts: TaskCounts }>()

const metrics: Array<{ key: keyof TaskCounts; label: string; tone?: string }> = [
  { key: 'total', label: '总行数' },
  { key: 'completed', label: '已完成' },
  { key: 'running', label: '进行中' },
  { key: 'pending', label: '待处理' },
  { key: 'success', label: '成功', tone: 'success' },
  { key: 'failed', label: '未查到有效商标', tone: 'danger' },
]
</script>

<template>
  <div class="task-summary" aria-label="任务统计">
    <div v-for="metric in metrics" :key="metric.key" class="metric">
      <span>{{ metric.label }}</span>
      <strong :class="metric.tone">{{ counts[metric.key] }}</strong>
    </div>
  </div>
</template>
