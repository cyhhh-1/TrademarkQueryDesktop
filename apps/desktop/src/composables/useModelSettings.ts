import { inject, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  createDesktopService,
  DESKTOP_SERVICE_KEY,
  type ModelSettings,
  type ModelSettingsState,
} from '../services/desktop'

export function useModelSettings() {
  const service = inject(DESKTOP_SERVICE_KEY, createDesktopService())
  const state = ref<ModelSettingsState | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const notice = ref('')
  let noticeTimer: ReturnType<typeof setTimeout> | null = null

  const load = async () => {
    loading.value = true
    error.value = ''
    try {
      state.value = await service.loadModelSettings()
      return true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '无法读取大模型配置'
      return false
    } finally {
      loading.value = false
    }
  }

  const ensureLoaded = async () => state.value !== null || load()

  const save = async (settings: ModelSettings) => {
    saving.value = true
    error.value = ''
    try {
      const saved = await service.saveModelSettings(settings)
      if (state.value) state.value = { ...state.value, current: saved }
      notice.value = '大模型配置已保存，下一个查询任务将使用新配置'
      if (noticeTimer !== null) clearTimeout(noticeTimer)
      noticeTimer = setTimeout(() => {
        notice.value = ''
        noticeTimer = null
      }, 3_500)
      return true
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '大模型配置保存失败'
      return false
    } finally {
      saving.value = false
    }
  }

  onMounted(() => {
    void load()
  })

  onBeforeUnmount(() => {
    if (noticeTimer !== null) clearTimeout(noticeTimer)
  })

  return { state, loading, saving, error, notice, ensureLoaded, save }
}
