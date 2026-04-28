import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "@/pages/LoginPage.vue";

describe("LoginPage", () => {
  it("renders Google and GitHub sign-in links pointing at the api", () => {
    const wrapper = mount(LoginPage);
    const google = wrapper.get('[data-testid="login-google"]');
    const github = wrapper.get('[data-testid="login-github"]');
    expect(google.attributes("href")).toContain("/auth/google/start");
    expect(github.attributes("href")).toContain("/auth/github/start");
  });
});

describe("auth store", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("populates currentUser when /auth/me returns 200", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "user-1",
          email: "alice@protos.dev",
          name: "Alice",
          memberships: [
            {
              tenant_id: "tenant-1",
              tenant_slug: "acme",
              tenant_name: "Acme",
              role: "owner",
            },
          ],
        }),
        { status: 200 },
      ),
    );
    const mod = await import("@/store/auth");
    await mod.loadCurrentUser();
    expect(mod.currentUser.value?.email).toBe("alice@protos.dev");
    expect(mod.authLoaded.value).toBe(true);
  });

  it("clears currentUser when /auth/me returns 401", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    const mod = await import("@/store/auth");
    await mod.loadCurrentUser();
    expect(mod.currentUser.value).toBeNull();
    expect(mod.authLoaded.value).toBe(true);
  });
});
