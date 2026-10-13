<script setup lang="ts">
import { getCurrentWindow } from '@tauri-apps/api/window'
import appIconUrl from '../assets/app-icon.svg?no-inline'

defineEmits<{ openSettings: [] }>()

async function windowAction(action: 'minimize' | 'maximize' | 'close') {
  if (!('__TAURI_INTERNALS__' in window)) return
  const appWindow = getCurrentWindow()
  if (action === 'minimize') await appWindow.minimize()
  if (action === 'maximize') await appWindow.toggleMaximize()
  if (action === 'close') await appWindow.close()
}

async function startWindowDrag(event: MouseEvent) {
  if (event.button !== 0 || (event.target as HTMLElement).closest('button')) return
  if (!('__TAURI_INTERNALS__' in window)) return
  await getCurrentWindow().startDragging()
}
</script>

<template>
  <header
    class="app-header"
    data-tauri-drag-region
    @mousedown="startWindowDrag"
    @dblclick="windowAction('maximize')"
  >
    <div class="brand" data-tauri-drag-region>
      <img class="brand-mark" :src="appIconUrl" alt="" data-tauri-drag-region />
      <strong data-tauri-drag-region>商标查询助手</strong>
      <span class="brand-divider" data-tauri-drag-region aria-hidden="true" />
      <span class="brand-subtitle" data-tauri-drag-region>完全本地运行</span>
    </div>
    <div class="header-actions">
      <button
        type="button"
        class="model-settings-button"
        data-testid="open-model-settings"
        aria-label="打开大模型设置"
        @dblclick.stop
        @click="$emit('openSettings')"
      >
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <circle cx="10" cy="10" r="3" />
          <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4" />
        </svg>
        模型设置
      </button>
      <div class="window-controls">
        <button type="button" aria-label="最小化" @dblclick.stop @click="windowAction('minimize')"><span class="minimize-mark" /></button>
        <button type="button" aria-label="最大化" @dblclick.stop @click="windowAction('maximize')"><span class="maximize-mark" /></button>
        <button type="button" aria-label="关闭" class="window-close" @dblclick.stop @click="windowAction('close')"><span class="close-mark" /></button>
      </div>
    </div>
  </header>
</template>
