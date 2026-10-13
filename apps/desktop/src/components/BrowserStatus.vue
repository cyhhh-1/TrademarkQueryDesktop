<script setup lang="ts">
import { computed } from 'vue'

import type { BrowserState } from '../domain/types'

const props = defineProps<{ state: BrowserState }>()

const view = computed(() => {
  const values: Partial<Record<BrowserState, { tone: string; label: string; description: string }>> = {
    READY: { tone: 'success', label: '可查询', description: '已检测到 Edge，且已登录商标网站' },
    LOGIN_REQUIRED: { tone: 'warning', label: '需要登录', description: '请在专用 Edge 中自行完成登录，再检查登录状态' },
    CAPTCHA_REQUIRED: { tone: 'warning', label: '需要验证', description: '请在 Edge 中完成人工验证' },
    AUTH_BLOCKED: { tone: 'danger', label: '登录已失效', description: '请从商标网站首页重新进入登录' },
    WAF_BLOCKED: { tone: 'danger', label: '访问受限', description: '网站拒绝访问，请稍后重试' },
    RETURNED_HOME: { tone: 'warning', label: '已返回首页', description: '再次检查后，程序会进入商标综合查询' },
  }
  return values[props.state] ?? { tone: 'muted', label: '未连接', description: '打开专用 Edge 后检测登录状态' }
})
</script>

<template>
  <div class="browser-state" :class="`tone-${view.tone}`">
    <div class="browser-state-label">
      <span class="state-icon" aria-hidden="true">
        <svg v-if="view.tone === 'success'" viewBox="0 0 20 20"><path d="m5 10 3 3 7-7" /></svg>
        <svg v-else viewBox="0 0 20 20"><path d="M10 6v5m0 3h.01" /></svg>
      </span>
      <strong>{{ view.label }}</strong>
    </div>
    <p>{{ view.description }}</p>
  </div>
</template>
