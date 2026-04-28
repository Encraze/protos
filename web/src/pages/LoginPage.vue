<script setup lang="ts">
import { startLoginUrl } from "@/api/auth";
</script>

<template>
  <main class="min-h-screen bg-canvas flex items-center justify-center px-6">
    <section class="w-full max-w-sm rounded-lg border border-outline bg-raised p-6">
      <header class="flex items-center gap-3">
        <span class="inline-block h-2.5 w-2.5 rounded-full bg-brand"></span>
        <h1 class="text-xl font-semibold tracking-tight text-text">Protos</h1>
      </header>
      <p class="mt-3 text-sm text-text-secondary">Sign in to continue.</p>
      <div class="mt-6 flex flex-col gap-2">
        <a
          :href="startLoginUrl('google')"
          data-testid="login-google"
          class="block rounded-md border border-outline bg-canvas px-4 py-2.5 text-center text-sm text-text hover:border-brand transition-colors"
        >Sign in with Google</a>
        <a
          :href="startLoginUrl('github')"
          data-testid="login-github"
          class="block rounded-md border border-outline bg-canvas px-4 py-2.5 text-center text-sm text-text hover:border-brand transition-colors"
        >Sign in with GitHub</a>
      </div>
    </section>
  </main>
</template>
