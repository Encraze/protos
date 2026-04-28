<script setup lang="ts">
import { onMounted, ref } from "vue";
import { fetchHealth, type HealthStatus } from "@/api/health";

const status = ref<HealthStatus | "loading">("loading");

onMounted(async () => {
  status.value = await fetchHealth();
});
</script>

<template>
  <main class="min-h-screen bg-canvas px-8 py-10">
    <header class="flex items-center gap-3">
      <span class="inline-block h-2.5 w-2.5 rounded-full bg-brand"></span>
      <h1 class="text-2xl font-semibold tracking-tight text-text">Protos Console</h1>
    </header>

    <section class="mt-10 max-w-md rounded-lg border border-outline bg-raised p-5">
      <p class="text-xs font-medium uppercase tracking-wider text-text-tertiary">API status</p>
      <p class="mt-2 flex items-center gap-2 text-base">
        <span
          class="inline-block h-2 w-2 rounded-full"
          :class="{
            'bg-success': status === 'ok',
            'bg-danger': status === 'error',
            'bg-text-tertiary animate-pulse': status === 'loading',
          }"
        ></span>
        <span
          data-testid="status"
          class="font-mono text-sm"
          :class="{
            'text-success': status === 'ok',
            'text-danger': status === 'error',
            'text-text-secondary': status === 'loading',
          }"
        >{{ status }}</span>
      </p>
    </section>
  </main>
</template>
