<script setup lang="ts">
import { ref } from "vue";
import { logout as apiLogout } from "@/api/auth";
import { clearCurrentUser, currentUser } from "@/store/auth";

const open = ref(false);

async function handleLogout() {
  await apiLogout();
  clearCurrentUser();
  window.location.assign("/login");
}
</script>

<template>
  <div v-if="currentUser" class="relative">
    <button
      type="button"
      data-testid="user-menu-button"
      class="flex items-center gap-2 rounded-md border border-outline bg-raised px-3 py-1.5 text-sm hover:border-brand transition-colors"
      @click="open = !open"
    >
      <span
        class="inline-flex h-6 w-6 items-center justify-center rounded-full bg-brand-subtle text-brand text-xs font-medium"
      >{{ currentUser.email.charAt(0).toUpperCase() }}</span>
      <span class="text-text" data-testid="user-menu-email">{{ currentUser.email }}</span>
    </button>
    <div
      v-if="open"
      class="absolute right-0 mt-2 w-56 rounded-md border border-outline bg-raised shadow-lg z-10"
    >
      <button
        type="button"
        data-testid="user-menu-logout"
        class="block w-full text-left px-4 py-2 text-sm text-text hover:bg-sunken"
        @click="handleLogout"
      >Log out</button>
    </div>
  </div>
</template>
