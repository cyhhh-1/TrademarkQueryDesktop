<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import type { ModelSettings } from '../services/desktop'

const props = defineProps<{
  settings: ModelSettings
  defaults: ModelSettings
  saving: boolean
  error?: string
}>()

const emit = defineEmits<{
  close: []
  save: [settings: ModelSettings]
}>()

const draft = reactive<ModelSettings>({ ...props.settings })
const showApiKey = ref(false)

watch(
  () => props.settings,
  (settings) => Object.assign(draft, settings),
  { deep: true },
)

const validationError = computed(() => {
  const url = draft.DASHSCOPE_BASE_URL.trim()
  if (!(url.startsWith('https://') || url.startsWith('http://'))) {
    return '接口 URL 必须以 http:// 或 https:// 开头'
  }
  if (!draft.DASHSCOPE_API_KEY.trim()) return 'API Key 不能为空'
  if (!draft.DASHSCOPE_MODEL.trim()) return '模型名称不能为空'
  return ''
})

const restoreDefaults = () => {
  Object.assign(draft, props.defaults)
}

const submit = () => {
  if (validationError.value || props.saving) return
  emit('save', {
    DASHSCOPE_BASE_URL: draft.DASHSCOPE_BASE_URL.trim().replace(/\/+$/, ''),
    DASHSCOPE_API_KEY: draft.DASHSCOPE_API_KEY.trim(),
    DASHSCOPE_MODEL: draft.DASHSCOPE_MODEL.trim(),
  })
}
</script>

<template>
  <div class="settings-backdrop" role="presentation">
    <section
      class="settings-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-settings-title"
      data-testid="model-settings-dialog"
    >
      <header class="settings-dialog-header">
        <div>
          <h2 id="model-settings-title">大模型设置</h2>
          <p>公司默认配置已经预置，普通员工无需修改。</p>
        </div>
        <button type="button" class="dialog-close" aria-label="关闭设置" :disabled="saving" @click="emit('close')">×</button>
      </header>

      <form class="settings-form" @submit.prevent="submit">
        <label for="model-base-url">接口 URL</label>
        <input
          id="model-base-url"
          v-model="draft.DASHSCOPE_BASE_URL"
          data-testid="model-base-url"
          autocomplete="off"
          spellcheck="false"
        />

        <label for="model-api-key">API Key</label>
        <div class="secret-input">
          <input
            id="model-api-key"
            v-model="draft.DASHSCOPE_API_KEY"
            :type="showApiKey ? 'text' : 'password'"
            data-testid="model-api-key"
            autocomplete="off"
            spellcheck="false"
          />
          <button
            type="button"
            data-testid="toggle-api-key"
            :aria-label="showApiKey ? '隐藏 API Key' : '显示 API Key'"
            @click="showApiKey = !showApiKey"
          >
            {{ showApiKey ? '隐藏' : '显示' }}
          </button>
        </div>

        <label for="model-name">模型名称</label>
        <input
          id="model-name"
          v-model="draft.DASHSCOPE_MODEL"
          data-testid="model-name"
          autocomplete="off"
          spellcheck="false"
        />

        <p class="settings-help">该配置仅在网页中的商标文字需要图片识别时使用，保存后从下一个查询任务生效。</p>
        <p v-if="validationError || error" class="settings-error" role="alert">{{ validationError || error }}</p>

        <footer class="settings-actions">
          <button
            type="button"
            class="button restore-button"
            data-testid="restore-model-defaults"
            :disabled="saving"
            @click="restoreDefaults"
          >
            恢复公司默认
          </button>
          <span class="settings-action-spacer" />
          <button type="button" class="button" data-testid="model-settings-cancel" :disabled="saving" @click="emit('close')">取消</button>
          <button
            type="submit"
          class="button primary"
          data-testid="save-model-settings"
          :disabled="saving || Boolean(validationError)"
          @click.prevent="submit"
        >
            {{ saving ? '正在保存…' : '保存配置' }}
          </button>
        </footer>
      </form>
    </section>
  </div>
</template>
