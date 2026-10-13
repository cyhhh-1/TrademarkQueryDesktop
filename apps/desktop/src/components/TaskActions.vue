<script setup lang="ts">
defineProps<{ canPause: boolean; canResume: boolean; canCancel: boolean; canSave: boolean }>()
defineEmits<{ pause: []; resume: []; cancel: []; save: [] }>()
</script>

<template>
  <div class="task-actions">
    <button type="button" class="button secondary" data-testid="pause-task" :disabled="!canPause" @click="$emit('pause')">
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 5h3v10H6zM11 5h3v10h-3z" /></svg>
      暂停任务
    </button>
    <button type="button" class="button secondary" data-testid="resume-task" :disabled="!canResume" @click="$emit('resume')">
      <svg class="play-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="m7 5 8 5-8 5z" /></svg>
      继续
    </button>
    <button type="button" class="button secondary" data-testid="cancel-task" :disabled="!canCancel" @click="$emit('cancel')">
      <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="6" y="6" width="8" height="8" /></svg>
      取消任务
    </button>
    <button type="button" class="button secondary save-button" data-testid="save-as" :class="{ primary: canSave }" :disabled="!canSave" @click="$emit('save')">
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 3h10l2 2v12H4zM7 3v5h6V3M7 17v-6h6v6" /></svg>
      另存为
    </button>
  </div>
</template>
