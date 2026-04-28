<script setup lang="ts">
import UserMenu from "@/components/UserMenu.vue";
</script>

<template>
  <header
    class="flex items-center justify-between border-b border-outline px-8 py-4"
  >
    <div class="flex items-center gap-6">
      <div class="flex items-center gap-3">
        <span class="inline-block h-2.5 w-2.5 rounded-full bg-brand"></span>
        <h1 class="text-lg font-semibold tracking-tight text-text">Protos Console</h1>
      </div>
      <nav class="flex items-center gap-4 text-sm">
        <RouterLink
          to="/"
          class="text-text-secondary hover:text-text"
          active-class="text-text"
        >Overview</RouterLink>
        <RouterLink
          to="/keys/providers"
          class="text-text-secondary hover:text-text"
          active-class="text-text"
        >Provider keys</RouterLink>
        <RouterLink
          to="/keys/platform"
          class="text-text-secondary hover:text-text"
          active-class="text-text"
        >API keys</RouterLink>
      </nav>
    </div>
    <UserMenu />
  </header>
</template>
