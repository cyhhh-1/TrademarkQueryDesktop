<script setup lang="ts">
import { nextTick, watch } from 'vue'

import type { RowPhase, TaskRow } from '../domain/types'

const props = defineProps<{ rows: TaskRow[] }>()

const phaseLabels: Record<RowPhase, string> = {
  pending: '待查询',
  retry_pending: '等待重试',
  running: '查询中',
  paused: '已暂停',
  success: '已完成',
  failed: '需处理',
}

watch(
  () => props.rows.find((row) => row.phase === 'running')?.row,
  async (rowNumber) => {
    if (!rowNumber) return
    await nextTick()
    const element = document.querySelector(`[data-row="${rowNumber}"]`)
    if (element instanceof HTMLElement && typeof element.scrollIntoView === 'function') {
      element.scrollIntoView({ block: 'nearest' })
    }
  },
)
</script>

<template>
  <div class="task-table-wrap">
    <table class="task-table">
      <thead><tr><th>行号</th><th>商标</th><th>商标申请人</th><th>阶段</th><th>结果</th></tr></thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.row"
          :data-row="row.row"
          :class="{ 'is-active': row.phase === 'running' }"
          :aria-current="row.phase === 'running' ? 'step' : undefined"
        >
          <td>{{ row.row }}</td>
          <td class="mark-cell">{{ row.mark }}</td>
          <td class="applicant-cell">{{ row.applicant || '-' }}</td>
          <td><span class="phase"><i :class="`phase-${row.phase}`" />{{ phaseLabels[row.phase] }}</span></td>
          <td>{{ row.result }}</td>
        </tr>
        <tr v-if="rows.length === 0" class="empty-row">
          <td colspan="5">选择 XLSX 后，这里会显示待查询商标</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
