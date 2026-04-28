import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/pages/HomePage.vue";

const stubs = { Topbar: true };

describe("HomePage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows ok when api responds 200", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );
    const wrapper = mount(HomePage, { global: { stubs } });
    await flushPromises();
    expect(wrapper.get('[data-testid="status"]').text()).toBe("ok");
  });

  it("shows error when api responds 503", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ status: "error" }), { status: 503 }),
    );
    const wrapper = mount(HomePage, { global: { stubs } });
    await flushPromises();
    expect(wrapper.get('[data-testid="status"]').text()).toBe("error");
  });
});
