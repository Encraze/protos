<script setup lang="ts">
import { onMounted, ref } from "vue";
import { apiKeysApi, type ApiKey, type ApiKeyMode } from "@/api/keys";
import RevealModal from "@/components/RevealModal.vue";
import Topbar from "@/components/Topbar.vue";

const keys = ref<ApiKey[]>([]);
const loading = ref(true);
const submitting = ref(false);
const formError = ref<string | null>(null);

const name = ref("");
const mode = ref<ApiKeyMode>("live");
const justIssuedToken = ref<string | null>(null);

async function load() {
  loading.value = true;
  try {
    keys.value = await apiKeysApi.list();
  } finally {
    loading.value = false;
  }
}

async function submit() {
  formError.value = null;
  submitting.value = true;
  try {
    const issued = await apiKeysApi.create({
      name: name.value,
      mode: mode.value,
    });
    justIssuedToken.value = issued.token;
    name.value = "";
    await load();
  } catch (err) {
    formError.value = (err as Error).message;
  } finally {
    submitting.value = false;
  }
}

async function revoke(key: ApiKey) {
  if (!window.confirm(`Revoke "${key.name}"? This cannot be undone.`)) return;
  await apiKeysApi.revoke(key.id);
  await load();
}

function dismissReveal() {
  justIssuedToken.value = null;
}

onMounted(load);
</script>

<template>
  <main class="min-h-screen bg-canvas">
    <Topbar />
    <section class="px-8 py-10">
      <header>
        <h2 class="text-xl font-semibold tracking-tight text-text">API keys</h2>
        <p class="mt-1 text-sm text-text-secondary">
          Platform tokens used to call the Protos gateway. Shown once at creation; we only store a hash.
        </p>
      </header>

      <form
        data-testid="api-key-form"
        class="mt-6 max-w-2xl rounded-lg border border-outline bg-raised p-5"
        @submit.prevent="submit"
      >
        <h3 class="text-sm font-medium text-text">Issue a new key</h3>
        <div class="mt-4 grid grid-cols-3 gap-3">
          <label class="col-span-2 text-xs uppercase tracking-wider text-text-tertiary">
            Name
            <input
              v-model="name"
              required
              maxlength="120"
              placeholder="e.g. ci-pipeline"
              class="mt-1 block w-full rounded-md border border-outline bg-canvas px-2 py-1.5 text-sm text-text"
            />
          </label>
          <label class="col-span-1 text-xs uppercase tracking-wider text-text-tertiary">
            Mode
            <select
              v-model="mode"
              class="mt-1 block w-full rounded-md border border-outline bg-canvas px-2 py-1.5 text-sm text-text"
            >
              <option value="live">Live</option>
              <option value="sandbox">Sandbox</option>
            </select>
          </label>
        </div>
        <p v-if="formError" class="mt-2 text-sm text-danger" data-testid="form-error">{{ formError }}</p>
        <div class="mt-4 flex justify-end">
          <button
            type="submit"
            :disabled="submitting"
            data-testid="api-key-submit"
            class="rounded-md bg-brand px-4 py-2 text-sm font-medium text-text-inverse disabled:cursor-not-allowed disabled:opacity-50"
          >{{ submitting ? "Issuing…" : "Issue key" }}</button>
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
          data-testid="api-keys-empty"
        >No API keys yet.</div>
        <table v-else class="w-full text-sm" data-testid="api-keys-table">
          <thead class="border-b border-outline text-xs uppercase tracking-wider text-text-tertiary">
            <tr>
              <th class="px-5 py-2 text-left">Name</th>
              <th class="px-5 py-2 text-left">Prefix</th>
              <th class="px-5 py-2 text-left">Mode</th>
              <th class="px-5 py-2 text-left">Last used</th>
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
              <td class="px-5 py-3 text-text">{{ k.name }}</td>
              <td class="px-5 py-3 font-mono text-text-secondary">{{ k.prefix }}…</td>
              <td class="px-5 py-3">
                <span
                  class="rounded-pill px-2 py-0.5 text-xs"
                  :class="k.mode === 'live' ? 'bg-brand-subtle text-brand' : 'bg-warning/10 text-warning'"
                >{{ k.mode }}</span>
              </td>
              <td class="px-5 py-3 text-text-secondary">
                {{ k.last_used_at ?? "never" }}
              </td>
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

    <RevealModal
      v-if="justIssuedToken"
      :token="justIssuedToken"
      title="Save your API key"
      @dismiss="dismissReveal"
    />
  </main>
</template>
