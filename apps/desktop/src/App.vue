<script setup lang="ts">
import { computed, ref } from 'vue'

import AppHeader from './components/AppHeader.vue'
import ModelSettingsDialog from './components/ModelSettingsDialog.vue'
import StepRail from './components/StepRail.vue'
import TaskActions from './components/TaskActions.vue'
import TaskProgress from './components/TaskProgress.vue'
import TaskSummary from './components/TaskSummary.vue'
import TaskTable from './components/TaskTable.vue'
import WorkflowSidebar from './components/WorkflowSidebar.vue'
import { useTrademarkTask } from './composables/useTrademarkTask'
import { useModelSettings } from './composables/useModelSettings'

const task = useTrademarkTask()
const modelSettings = useModelSettings()
const settingsOpen = ref(false)

const openModelSettings = async () => {
  if (await modelSettings.ensureLoaded()) settingsOpen.value = true
}

const saveModelSettings = async (settings: Parameters<typeof modelSettings.save>[0]) => {
  if (await modelSettings.save(settings)) settingsOpen.value = false
}

const currentStep = computed(() => {
  if (task.state.value.taskState === 'completed') return 4
  if (['running', 'paused_user_action'].includes(task.state.value.taskState)) return 3
  if (task.state.value.file) return 2
  return 1
})

const status = computed(() => {
  if (task.state.value.taskState === 'completed') return ['查询完成', '结果已安全保存到本地工作目录']
  if (task.state.value.taskState === 'cancelled') return ['查询已停止', '已完成的商标已保存，可以点击“另存为”保留阶段性结果']
  if (task.state.value.taskState === 'cancelling') return ['正在安全停止', '当前商标完成写入后任务会停止，请稍候']
  if (task.state.value.taskState === 'running') return ['正在后台查询', '可以最小化客户端并继续处理其他工作']
  if (task.state.value.taskState === 'pausing') return ['正在安全暂停', '当前步骤完成后会暂停，请稍候']
  if (task.state.value.taskState === 'paused_user_action') {
    return task.state.value.browserState === 'READY'
      ? ['任务已暂停', '登录状态已确认，点击“继续”即可恢复后台查询']
      : ['任务已暂停', '请在专用 Edge 中重新登录，确认登录状态后再继续']
  }
  if (task.state.value.file && task.state.value.browserState === 'READY') return ['准备就绪', 'Edge 已登录，可以开始查询']
  if (task.state.value.file) return ['等待登录网站', '打开 Edge 并完成商标网站登录']
  return ['选择查询表格', '支持商标申请和商标唯一性两种 XLSX 模板']
})
</script>

<template>
  <div class="app-shell">
    <AppHeader @open-settings="openModelSettings" />
    <StepRail :current="currentStep" />
    <main class="workspace">
      <WorkflowSidebar
        :file="task.state.value.file"
        :browser-state="task.state.value.browserState"
        :can-start="task.canStart.value"
        :file-locked="!task.canChangeFile.value"
        :browser-locked="task.browserLocked.value"
        :browser-opening="task.browserOpening.value"
        :browser-checking="task.browserChecking.value"
        :query-active="task.queryActive.value"
        @select="task.selectFile"
        @clear="task.clearFile"
        @open-browser="task.openBrowser"
        @connect-browser="task.connectBrowser"
        @start="task.start"
      />
      <section class="task-panel">
        <div
          v-if="task.state.value.taskState === 'paused_user_action' && task.state.value.alert"
          class="interruption-banner"
          role="alert"
        >
          <span class="banner-icon" aria-hidden="true">!</span>
          <strong>{{ task.state.value.alert }}</strong>
        </div>
        <div v-else-if="task.state.value.alert" class="error-banner" role="alert">{{ task.state.value.alert }}</div>
        <div class="status-heading">
          <h1>{{ status[0] }}</h1>
          <p>{{ status[1] }}</p>
        </div>
        <TaskProgress
          :progress="task.state.value.progress"
          :counts="task.counts.value"
          :task-state="task.state.value.taskState"
          :elapsed-seconds="task.elapsedSeconds.value"
          :eta-seconds="task.etaSeconds.value"
        />
        <TaskSummary :counts="task.counts.value" />
        <TaskTable :rows="task.state.value.rows" />
        <TaskActions
          :can-pause="task.canPause.value"
          :can-resume="task.canResume.value"
          :can-cancel="task.canCancel.value"
          :can-save="task.canSave.value"
          @pause="task.pause"
          @resume="task.resume"
          @cancel="task.cancel"
          @save="task.saveAs"
        />
      </section>
    </main>
    <div
      v-if="modelSettings.notice.value || (!settingsOpen && modelSettings.error.value)"
      class="settings-notice"
      :class="{ error: modelSettings.error.value }"
      data-testid="model-settings-notice"
      role="status"
    >
      {{ modelSettings.error.value || modelSettings.notice.value }}
    </div>
    <ModelSettingsDialog
      v-if="settingsOpen && modelSettings.state.value"
      :settings="modelSettings.state.value.current"
      :defaults="modelSettings.state.value.defaults"
      :saving="modelSettings.saving.value"
      :error="modelSettings.error.value"
      @close="settingsOpen = false"
      @save="saveModelSettings"
    />
  </div>
</template>
