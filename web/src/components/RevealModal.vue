<script setup lang="ts">
import { ref } from "vue";

defineProps<{
  token: string;
  title?: string;
  description?: string;
}>();

const emit = defineEmits<{ dismiss: [] }>();

const acknowledged = ref(false);
const copied = ref(false);

async function copy(value: string) {
  try {
    await navigator.clipboard.writeText(value);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    copied.value = false;
  }
}
</script>

<template>
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-6"
    data-testid="reveal-modal"
  >
    <div class="w-full max-w-md rounded-lg border border-outline bg-raised p-6">
      <h2 class="text-base font-semibold text-text">
        {{ title ?? "Save your key" }}
      </h2>
      <p class="mt-2 text-sm text-text-secondary">
        {{
          description ??
          "This token is shown once. Copy it and store it somewhere safe — you won't be able to see it again."
        }}
      </p>
      <div
        class="mt-4 flex items-center gap-2 rounded-md border border-outline bg-canvas p-3"
      >
        <code data-testid="reveal-token" class="flex-1 break-all font-mono text-sm text-text">{{ token }}</code>
        <button
          type="button"
          data-testid="reveal-copy"
          class="rounded border border-outline px-2 py-1 text-xs text-text-secondary hover:border-brand hover:text-text"
          @click="copy(token)"
        >{{ copied ? "Copied" : "Copy" }}</button>
      </div>
      <label class="mt-4 flex items-center gap-2 text-sm text-text-secondary">
        <input
          v-model="acknowledged"
          type="checkbox"
          data-testid="reveal-ack"
          class="h-4 w-4 rounded border-outline bg-canvas"
        />
        I've stored this token securely
      </label>
      <div class="mt-4 flex justify-end">
        <button
          type="button"
          :disabled="!acknowledged"
          data-testid="reveal-dismiss"
          class="rounded-md bg-brand px-4 py-2 text-sm font-medium text-text-inverse disabled:cursor-not-allowed disabled:opacity-50"
          @click="emit('dismiss')"
        >Done</button>
      </div>
    </div>
  </div>
</template>
