<script setup lang="ts">
import { computed } from 'vue'

import { stageLabel } from '../domain/taskState'
import type { TaskCounts, TaskProgress, TaskState } from '../domain/types'

const props = defineProps<{
  progress: TaskProgress
  counts: TaskCounts
  taskState: TaskState
  elapsedSeconds: number
  etaSeconds: number | null
}>()

const percent = computed(() => props.counts.total > 0
  ? Math.round((props.counts.completed / props.counts.total) * 100)
  : 0)

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  if (minutes === 0) return `${remainder}秒`
  return `${minutes}分${String(remainder).padStart(2, '0')}秒`
}
</script>

<template>
  <section v-if="counts.total > 0" class="task-progress" data-testid="task-progress" aria-live="polite">
    <div class="progress-headline">
      <div>
        <span class="progress-eyebrow">当前处理</span>
        <strong>{{ progress.currentMark || '等待开始' }}</strong>
      </div>
      <span class="progress-position">
        <template v-if="progress.current > 0">第 {{ progress.current }} 个，共 {{ progress.total || counts.total }} 个</template>
        <template v-else>尚未开始，共 {{ progress.total || counts.total }} 个</template>
      </span>
    </div>
    <div
      class="progress-track"
      role="progressbar"
      aria-label="查询完成进度"
      :aria-valuemin="0"
      :aria-valuemax="100"
      :aria-valuenow="percent"
    >
      <span :style="{ width: `${percent}%` }" />
    </div>
    <div class="progress-meta">
      <span><b>完成进度</b>{{ percent }}%</span>
      <span><b>当前阶段</b>{{ stageLabel(progress.stage) }}</span>
      <span><b>查询轮次</b>{{ progress.passNumber === 2 ? '第 2 轮重试' : '第 1 轮查询' }}</span>
      <span><b>结果页</b>{{ progress.page && progress.pages ? `第 ${progress.page} / ${progress.pages} 页` : '等待结果页' }}</span>
      <span><b>已用时间</b>{{ formatDuration(elapsedSeconds) }}</span>
      <span><b>预计剩余</b>{{ etaSeconds === null ? '完成首个商标后开始估算' : formatDuration(etaSeconds) }}</span>
    </div>
  </section>
</template>
