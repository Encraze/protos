<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  providerKeysApi,
  type ProviderKey,
  type ProviderName,
} from "@/api/keys";
import Topbar from "@/components/Topbar.vue";

const keys = ref<ProviderKey[]>([]);
const loading = ref(true);
const submitting = ref(false);
const formError = ref<string | null>(null);

const provider = ref<ProviderName>("openai");
const label = ref("");
const secret = ref("");

async function load() {
  loading.value = true;
  try {
    keys.value = await providerKeysApi.list();
  } finally {
    loading.value = false;
  }
}

async function submit() {
  formError.value = null;
  submitting.value = true;
  try {
    await providerKeysApi.create({
      provider: provider.value,
      label: label.value,
      secret: secret.value,
    });
    label.value = "";
    secret.value = "";
    await load();
  } catch (err) {
    formError.value = (err as Error).message;
  } finally {
    submitting.value = false;
  }
}

async function revoke(key: ProviderKey) {
  if (!window.confirm(`Revoke "${key.label}"? This cannot be undone.`)) return;
  await providerKeysApi.revoke(key.id);
  await load();
}

onMounted(load);
</script>

<template>
  <main class="min-h-screen bg-canvas">
    <Topbar />
    <section class="px-8 py-10">
      <header>
        <h2 class="text-xl font-semibold tracking-tight text-text">Provider keys</h2>
        <p class="mt-1 text-sm text-text-secondary">
          Upstream credentials for OpenAI, Anthropic, and Gemini. Stored encrypted; only the last four characters are ever displayed.
        </p>
      </header>

      <form
        data-testid="provider-key-form"
        class="mt-6 max-w-2xl rounded-lg border border-outline bg-raised p-5"
        @submit.prevent="submit"
      >
        <h3 class="text-sm font-medium text-text">Add a new key</h3>
        <div class="mt-4 grid grid-cols-3 gap-3">
          <label class="col-span-1 text-xs uppercase tracking-wider text-text-tertiary">
            Provider
            <select
              v-model="provider"
              class="mt-1 block w-full rounded-md border border-outline bg-canvas px-2 py-1.5 text-sm text-text"
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="gemini">Gemini</option>
            </select>
          </label>
          <label class="col-span-2 text-xs uppercase tracking-wider text-text-tertiary">
            Label
            <input
              v-model="label"
              required
              maxlength="120"
              placeholder="e.g. prod-openai"
              class="mt-1 block w-full rounded-md border border-outline bg-canvas px-2 py-1.5 text-sm text-text"
            />
          </label>
        </div>
        <label class="mt-3 block text-xs uppercase tracking-wider text-text-tertiary">
          Secret
          <input
            v-model="secret"
            type="password"
            required
            minlength="8"
            data-testid="provider-key-secret"
            class="mt-1 block w-full rounded-md border border-outline bg-canvas px-2 py-1.5 font-mono text-sm text-text"
          />
        </label>
        <p v-if="formError" class="mt-2 text-sm text-danger" data-testid="form-error">{{ formError }}</p>
        <div class="mt-4 flex justify-end">
          <button
            type="submit"
            :disabled="submitting"
            data-testid="provider-key-submit"
            class="rounded-md bg-brand px-4 py-2 text-sm font-medium text-text-inverse disabled:cursor-not-allowed disabled:opacity-50"
          >{{ submitting ? "Saving…" : "Add key" }}</button>
        </div>
      </form>

      <div class="mt-8 max-w-3xl rounded-lg border border-outline bg-raised">
        <header class="flex items-center justify-between border-b border-outline px-5 py-3">
          <h3 class="text-sm font-medium text-text">Existing keys</h3>
          <button
            type="button"
            class="text-xs text-text-secondary hover:text-text"
            @click="load"
          >Refresh</button>
        </header>
        <div v-if="loading" class="px-5 py-6 text-sm text-text-secondary">Loading…</div>
        <div
          v-else-if="keys.length === 0"
          class="px-5 py-6 text-sm text-text-secondary"
          data-testid="provider-keys-empty"
        >No provider keys yet.</div>
        <table v-else class="w-full text-sm" data-testid="provider-keys-table">
          <thead class="border-b border-outline text-xs uppercase tracking-wider text-text-tertiary">
            <tr>
              <th class="px-5 py-2 text-left">Label</th>
              <th class="px-5 py-2 text-left">Provider</th>
              <th class="px-5 py-2 text-left">Last 4</th>
              <th class="px-5 py-2 text-left">Status</th>
              <th class="px-5 py-2"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="k in keys"
              :key="k.id"
              class="border-b border-outline last:border-b-0"
            >
              <td class="px-5 py-3 text-text">{{ k.label }}</td>
              <td class="px-5 py-3 text-text-secondary">{{ k.provider }}</td>
              <td class="px-5 py-3 font-mono text-text-secondary">{{ k.last_four }}</td>
              <td class="px-5 py-3">
                <span v-if="k.revoked_at" class="text-danger">revoked</span>
                <span v-else class="text-success">active</span>
              </td>
              <td class="px-5 py-3 text-right">
                <button
                  v-if="!k.revoked_at"
                  type="button"
                  class="text-xs text-danger hover:underline"
                  @click="revoke(k)"
                >Revoke</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>
