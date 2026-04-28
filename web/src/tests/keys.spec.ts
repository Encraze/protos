import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProviderKeysPage from "@/pages/ProviderKeysPage.vue";
import ApiKeysPage from "@/pages/ApiKeysPage.vue";

const stubs = { Topbar: true, RouterLink: true };

function mockFetchSequence(responses: Response[]) {
  const fetchMock = vi.fn();
  for (const r of responses) fetchMock.mockResolvedValueOnce(r);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ProviderKeysPage", () => {
  beforeEach(() => {
    vi.stubGlobal("confirm", vi.fn(() => true));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("submits the form and reloads the list", async () => {
    const fetchMock = mockFetchSequence([
      new Response(JSON.stringify([]), { status: 200 }),
      new Response(
        JSON.stringify({
          id: "pk-1",
          provider: "openai",
          label: "prod",
          last_four: "tail",
          revoked_at: null,
          created_at: "2026-04-29T00:00:00Z",
        }),
        { status: 201 },
      ),
      new Response(
        JSON.stringify([
          {
            id: "pk-1",
            provider: "openai",
            label: "prod",
            last_four: "tail",
            revoked_at: null,
            created_at: "2026-04-29T00:00:00Z",
          },
        ]),
        { status: 200 },
      ),
    ]);

    const wrapper = mount(ProviderKeysPage, { global: { stubs } });
    await flushPromises();

    await wrapper.get('input[required][maxlength="120"]').setValue("prod");
    await wrapper.get('[data-testid="provider-key-secret"]').setValue("supersecret-1234");
    await wrapper.get('[data-testid="provider-key-form"]').trigger("submit.prevent");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(wrapper.find('[data-testid="provider-keys-table"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("tail");
    expect(wrapper.text()).toContain("prod");
  });

  it("calls revoke on the api when the user confirms", async () => {
    const fetchMock = mockFetchSequence([
      new Response(
        JSON.stringify([
          {
            id: "pk-1",
            provider: "openai",
            label: "to-revoke",
            last_four: "abcd",
            revoked_at: null,
            created_at: "2026-04-29T00:00:00Z",
          },
        ]),
        { status: 200 },
      ),
      new Response(
        JSON.stringify({
          id: "pk-1",
          provider: "openai",
          label: "to-revoke",
          last_four: "abcd",
          revoked_at: "2026-04-29T01:00:00Z",
          created_at: "2026-04-29T00:00:00Z",
        }),
        { status: 200 },
      ),
      new Response(
        JSON.stringify([
          {
            id: "pk-1",
            provider: "openai",
            label: "to-revoke",
            last_four: "abcd",
            revoked_at: "2026-04-29T01:00:00Z",
            created_at: "2026-04-29T00:00:00Z",
          },
        ]),
        { status: 200 },
      ),
    ]);

    const wrapper = mount(ProviderKeysPage, { global: { stubs } });
    await flushPromises();
    await wrapper.get("button.text-danger").trigger("click");
    await flushPromises();

    const revokeCall = fetchMock.mock.calls[1]![0] as string;
    expect(revokeCall).toContain("/v1/provider-keys/pk-1/revoke");
  });
});

describe("ApiKeysPage", () => {
  beforeEach(() => {
    vi.stubGlobal("confirm", vi.fn(() => true));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("issues a key and shows the reveal modal once", async () => {
    mockFetchSequence([
      new Response(JSON.stringify([]), { status: 200 }),
      new Response(
        JSON.stringify({
          id: "ak-1",
          name: "ci",
          prefix: "pk_live_abcdef12",
          scopes: [],
          mode: "live",
          expires_at: null,
          revoked_at: null,
          last_used_at: null,
          created_at: "2026-04-29T00:00:00Z",
          token: "pk_live_abcdef12FULLTOKEN",
        }),
        { status: 201 },
      ),
      new Response(
        JSON.stringify([
          {
            id: "ak-1",
            name: "ci",
            prefix: "pk_live_abcdef12",
            scopes: [],
            mode: "live",
            expires_at: null,
            revoked_at: null,
            last_used_at: null,
            created_at: "2026-04-29T00:00:00Z",
          },
        ]),
        { status: 200 },
      ),
    ]);

    const wrapper = mount(ApiKeysPage, { global: { stubs } });
    await flushPromises();

    await wrapper.get('input[required][maxlength="120"]').setValue("ci");
    await wrapper.get('[data-testid="api-key-form"]').trigger("submit.prevent");
    await flushPromises();

    expect(wrapper.find('[data-testid="reveal-modal"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="reveal-token"]').text()).toBe("pk_live_abcdef12FULLTOKEN");

    await wrapper.get('[data-testid="reveal-ack"]').setValue(true);
    await wrapper.get('[data-testid="reveal-dismiss"]').trigger("click");
    expect(wrapper.find('[data-testid="reveal-modal"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("pk_live_abcdef12");
  });
});
