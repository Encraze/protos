<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { fetchHealth, type HealthStatus } from "@/api/health";
import { currentUser } from "@/store/auth";
import UserMenu from "@/components/UserMenu.vue";

const status = ref<HealthStatus | "loading">("loading");

const primaryMembership = computed(() => currentUser.value?.memberships[0] ?? null);

onMounted(async () => {
  status.value = await fetchHealth();
});
</script>

<template>
  <main class="min-h-screen bg-canvas">
    <header
      class="flex items-center justify-between border-b border-outline px-8 py-4"
    >
      <div class="flex items-center gap-3">
        <span class="inline-block h-2.5 w-2.5 rounded-full bg-brand"></span>
        <h1 class="text-lg font-semibold tracking-tight text-text">
          Protos Console
        </h1>
      </div>
      <UserMenu />
    </header>

    <section class="px-8 py-10">
      <div v-if="primaryMembership" class="mb-8">
        <p
          class="text-xs font-medium uppercase tracking-wider text-text-tertiary"
        >Tenant</p>
        <p class="mt-1 text-text" data-testid="tenant-name">
          {{ primaryMembership.tenant_name }}
          <span class="text-text-secondary">({{ primaryMembership.role }})</span>
        </p>
      </div>

      <div class="max-w-md rounded-lg border border-outline bg-raised p-5">
        <p
          class="text-xs font-medium uppercase tracking-wider text-text-tertiary"
        >API status</p>
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
      </div>
    </section>
  </main>
</template>
