import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'

import { initialUiState, reduceWorkerEvent, taskCounts } from '../domain/taskState'
import type { WorkerEvent } from '../domain/types'
import { createDesktopService, DESKTOP_SERVICE_KEY } from '../services/desktop'


export function useTrademarkTask() {
  const service = inject(DESKTOP_SERVICE_KEY, createDesktopService())
  const state = ref(initialUiState())
  let unlisten: (() => void) | null = null
  let unlistenFileDrop: (() => void) | null = null
  let clockTimer: ReturnType<typeof setInterval> | null = null
  const clockMs = ref(Date.now())
  const activeStartedAt = ref<number | null>(null)
  const accumulatedMs = ref(0)
  const timedRunId = ref<string | null>(null)
  const browserChecking = ref(false)
  let browserCheckTimer: ReturnType<typeof setTimeout> | null = null
  const browserOpening = ref(false)
  let browserOpenTimer: ReturnType<typeof setTimeout> | null = null

  const finishBrowserOpen = () => {
    browserOpening.value = false
    if (browserOpenTimer !== null) {
      clearTimeout(browserOpenTimer)
      browserOpenTimer = null
    }
  }

  const finishBrowserCheck = () => {
    browserChecking.value = false
    if (browserCheckTimer !== null) {
      clearTimeout(browserCheckTimer)
      browserCheckTimer = null
    }
  }

  const stopTiming = () => {
    if (activeStartedAt.value !== null) {
      accumulatedMs.value += Date.now() - activeStartedAt.value
      activeStartedAt.value = null
    }
  }

  const dispatch = (event: WorkerEvent) => {
    if (event.type === 'browser_state' || event.type === 'error') finishBrowserCheck()
    if (['browser_state', 'browser_shown', 'error'].includes(event.type)) finishBrowserOpen()
    const shouldRecheckNewFile = event.type === 'file_inspected'
      && state.value.browserState !== 'EDGE_NOT_RUNNING'
    if (event.type === 'file_inspected') {
      stopTiming()
      accumulatedMs.value = 0
      activeStartedAt.value = null
      timedRunId.value = null
    }
    if (event.type === 'task_state' && event.state === 'running') {
      const runId = typeof event.run_id === 'string' ? event.run_id : state.value.runId
      if (runId && timedRunId.value !== runId) {
        timedRunId.value = runId
        accumulatedMs.value = 0
        activeStartedAt.value = null
      }
      if (activeStartedAt.value === null) activeStartedAt.value = Date.now()
    }
    if (
      event.type === 'paused'
      || event.type === 'completed'
      || event.type === 'error'
      || (event.type === 'task_state' && ['cancelled', 'completed', 'failed'].includes(String(event.state)))
    ) {
      stopTiming()
    }
    state.value = reduceWorkerEvent(state.value, event)
    if (shouldRecheckNewFile) {
      void perform(() => service.send({ type: 'browser_status', protocol: 1 }))
    }
    if (event.type === 'completed') {
      const counts = taskCounts(state.value.rows)
      void service.notify(
        '商标查询已完成',
        `成功 ${counts.success} 项，失败 ${counts.failed} 项；结果已保存在本机。`,
      ).catch(() => undefined)
    }
    if (
      event.type === 'paused'
      && ['LOGIN_REQUIRED', 'AUTH_BLOCKED', 'CAPTCHA_REQUIRED'].includes(String(event.reason))
    ) {
      void service.notify(
        '商标查询已暂停',
        `${typeof event.message === 'string' ? event.message : '商标网站要求重新登录'}；已保存阶段性结果，请完成登录后继续。`,
      ).catch(() => undefined)
    }
  }

  const perform = async (operation: () => Promise<void>) => {
    try {
      await operation()
    } catch (error) {
      dispatch({
        type: 'error',
        message: error instanceof Error ? error.message : '操作失败',
      })
    }
  }

  onMounted(async () => {
    clockTimer = setInterval(() => {
      clockMs.value = Date.now()
    }, 1000)
    unlisten = await service.listen(dispatch)
    await perform(() => service.startWorker())
    try {
      unlistenFileDrop = await service.listenFileDrop((paths) => {
        if (fileLocked.value) return
        const path = paths.find((candidate) => candidate.toLowerCase().endsWith('.xlsx'))
        if (!path) {
          dispatch({ type: 'error', message: '拖入的文件不是 XLSX 工作簿，请重新选择' })
          return
        }
        void inspectFile(path)
      })
    } catch {
      // File selection still works if an older WebView cannot expose native drag/drop events.
    }
  })

  onBeforeUnmount(() => {
    if (clockTimer !== null) clearInterval(clockTimer)
    finishBrowserCheck()
    finishBrowserOpen()
    unlisten?.()
    unlistenFileDrop?.()
    void service.stopWorker()
  })

  const fileLocked = computed(() => ['running', 'pausing', 'paused_user_action', 'cancelling'].includes(state.value.taskState))
  const queryActive = computed(() => ['running', 'pausing'].includes(state.value.taskState))
  const browserLocked = computed(() => browserChecking.value || browserOpening.value || state.value.taskState === 'cancelling')

  const inspectFile = (path: string) => perform(
    () => service.send({ type: 'inspect_file', protocol: 1, path }),
  )

  const selectFile = () => {
    if (fileLocked.value) return Promise.resolve()
    return perform(async () => {
      const path = await service.pickXlsx()
      if (path) await service.send({ type: 'inspect_file', protocol: 1, path })
    })
  }

  const downloadTemplate = (kind: 'application' | 'uniqueness') => perform(
    () => service.downloadTemplate(kind).then(() => undefined),
  )

  const clearFile = () => {
    if (fileLocked.value) return
    state.value = { ...initialUiState(), workerReady: state.value.workerReady, browserState: state.value.browserState }
  }

  const openBrowser = () => {
    if (browserOpening.value) return Promise.resolve()
    browserOpening.value = true
    browserOpenTimer = setTimeout(() => {
      if (!browserOpening.value) return
      finishBrowserOpen()
      dispatch({ type: 'error', message: '显示专用 Edge 超时，请重新点击打开商标网站' })
    }, 40_000)
    return perform(() => service.send({
      type: queryActive.value || state.value.taskState === 'paused_user_action'
        ? 'show_browser'
        : 'open_browser',
      protocol: 1,
    }))
  }
  const connectBrowser = () => {
    if (browserChecking.value) return Promise.resolve()
    browserChecking.value = true
    browserCheckTimer = setTimeout(() => {
      if (!browserChecking.value) return
      finishBrowserCheck()
      dispatch({
        type: 'error',
        message: '登录状态检查超时。请确认专用 Edge 已停留在商标综合检索页，然后重新检查。',
      })
    }, 30_000)
    return perform(() => service.send({ type: 'connect_browser', protocol: 1 }))
  }
  const recheckBrowser = () => perform(() => service.send({ type: 'browser_status', protocol: 1 }))
  const start = () => perform(async () => {
    if (!state.value.file) return
    const runId = `run-${new Date().toISOString().replace(/\D/g, '').slice(0, 17)}`
    await service.send({
      type: 'start',
      protocol: 1,
      run_id: runId,
      path: state.value.file.path,
      overwrite: false,
    })
  })
  const resume = () => perform(async () => {
    if (!state.value.runId) return
    await service.send({ type: 'resume', protocol: 1, run_id: state.value.runId })
  })
  const pause = () => perform(async () => {
    state.value = { ...state.value, taskState: 'pausing' }
    await service.send({ type: 'pause', protocol: 1 })
  })
  const cancel = () => perform(async () => {
    state.value = { ...state.value, taskState: 'cancelling' }
    await service.send({
      type: 'cancel',
      protocol: 1,
      ...(state.value.runId ? { run_id: state.value.runId } : {}),
    })
  })
  const saveAs = () => perform(async () => {
    if (!state.value.outputPath) return
    await service.saveOutputAs(state.value.outputPath, state.value.file?.path)
  })

  return {
    state,
    counts: computed(() => taskCounts(state.value.rows)),
    canStart: computed(() => Boolean(
      state.value.file && state.value.browserState === 'READY' && ['ready', 'waiting_browser'].includes(state.value.taskState),
    )),
    canPause: computed(() => state.value.taskState === 'running'),
    canResume: computed(() => state.value.taskState === 'paused_user_action' && state.value.browserState === 'READY'),
    canCancel: computed(() => ['running', 'pausing', 'paused_user_action'].includes(state.value.taskState)),
    canSave: computed(() => ['completed', 'cancelled'].includes(state.value.taskState) && Boolean(state.value.outputPath)),
    canChangeFile: computed(() => !fileLocked.value),
    browserLocked,
    browserOpening,
    browserChecking,
    queryActive,
    elapsedSeconds: computed(() => {
      const live = activeStartedAt.value === null ? 0 : clockMs.value - activeStartedAt.value
      return Math.max(0, Math.floor((accumulatedMs.value + live) / 1000))
    }),
    etaSeconds: computed(() => {
      const counts = taskCounts(state.value.rows)
      if (state.value.taskState !== 'running' || counts.completed < 1 || counts.total <= counts.completed) return null
      const elapsed = accumulatedMs.value
        + (activeStartedAt.value === null ? 0 : clockMs.value - activeStartedAt.value)
      return Math.max(0, Math.round((elapsed / 1000 / counts.completed) * (counts.total - counts.completed)))
    }),
    selectFile,
    downloadTemplate,
    clearFile,
    openBrowser,
    connectBrowser,
    recheckBrowser,
    start,
    pause,
    resume,
    cancel,
    saveAs,
  }
}
