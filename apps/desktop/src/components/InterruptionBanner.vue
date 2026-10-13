<script setup lang="ts">
defineProps<{ message: string }>()
defineEmits<{ recheck: [] }>()
</script>

<template>
  <div class="interruption-banner" role="status">
    <span class="banner-icon" aria-hidden="true">!</span>
    <strong>{{ message }}</strong>
    <button type="button" class="button secondary" data-testid="recheck-browser" @click="$emit('recheck')">重新检测</button>
  </div>
</template>
