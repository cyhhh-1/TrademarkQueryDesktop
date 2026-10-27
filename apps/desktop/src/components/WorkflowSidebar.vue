<script setup lang="ts">
import BrowserStatus from './BrowserStatus.vue'
import type { BrowserState, SelectedFile } from '../domain/types'

defineProps<{
  file: SelectedFile | null
  browserState: BrowserState
  canStart: boolean
  fileLocked: boolean
  browserLocked: boolean
  browserOpening: boolean
  browserChecking: boolean
  queryActive: boolean
}>()

defineEmits<{
  select: []
  clear: []
  openBrowser: []
  connectBrowser: []
  start: []
}>()

function formatBytes(bytes: number): string {
  if (!bytes) return '大小未知'
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}
</script>

<template>
  <aside class="workflow-sidebar">
    <section class="workflow-section">
      <h2>1. 选择表格</h2>
      <div v-if="file" class="file-card">
        <span class="file-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M6 2h8l4 4v16H6zM14 2v5h5M9 11l6 6m0-6-6 6" /></svg>
        </span>
        <span class="file-details">
          <strong>{{ file.name }}</strong>
          <small>{{ file.taskCount }} 行 · {{ formatBytes(file.size) }}</small>
        </span>
        <button type="button" class="icon-button" aria-label="移除文件" :disabled="fileLocked" @click="$emit('clear')">×</button>
      </div>
      <div v-else class="empty-file">尚未选择 XLSX 文件</div>
      <span class="field-label">检测到的表格模板</span>
      <div class="readonly-field" role="textbox" aria-readonly="true">{{ file?.templateName ?? '等待选择文件' }}</div>
      <button type="button" class="button secondary full-width" data-testid="select-file" :disabled="fileLocked" @click="$emit('select')">
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2.5 5.5h6l1.5 2h7.5v8.5h-15z" /></svg>
        选择 XLSX 或拖入窗口
      </button>
    </section>

    <section class="workflow-section">
      <h2>2. 登录网站</h2>
      <span class="field-label">Edge 浏览器状态</span>
      <BrowserStatus :state="browserState" />
      <button
        type="button"
        class="button secondary full-width"
        data-testid="open-browser"
        :disabled="browserLocked"
        @click="$emit('openBrowser')"
      >
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M11 3h6v6M17 3l-8 8M15 11v5H4V5h5" /></svg>
        {{ browserOpening ? '正在打开专用 Edge…' : queryActive ? '查看查询过程' : '打开商标网站' }}
      </button>
      <button
        v-if="browserState === 'LOGIN_REQUIRED'"
        type="button"
        class="button primary full-width"
        data-testid="connect-browser"
        :disabled="browserLocked"
        @click="$emit('connectBrowser')"
      >
        {{ browserChecking ? '正在检查登录状态…' : '我已登录，检查并进入查询' }}
      </button>
    </section>

    <section class="workflow-section workflow-start">
      <h2>3. 开始查询</h2>
      <button type="button" class="button primary full-width" data-testid="start-task" :disabled="!canStart" @click="$emit('start')">
        <svg class="play-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="m7 5 8 5-8 5z" /></svg>
        开始查询
      </button>
    </section>
  </aside>
</template>
