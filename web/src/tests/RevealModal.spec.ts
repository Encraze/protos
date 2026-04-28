import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import RevealModal from "@/components/RevealModal.vue";

describe("RevealModal", () => {
  it("renders the token", () => {
    const wrapper = mount(RevealModal, { props: { token: "pk_live_secret123" } });
    expect(wrapper.get('[data-testid="reveal-token"]').text()).toBe("pk_live_secret123");
  });

  it("disables Done until acknowledged", async () => {
    const wrapper = mount(RevealModal, { props: { token: "pk_live_secret" } });
    const dismiss = wrapper.get('[data-testid="reveal-dismiss"]');
    expect(dismiss.attributes("disabled")).toBeDefined();

    await wrapper.get('[data-testid="reveal-ack"]').setValue(true);
    expect(dismiss.attributes("disabled")).toBeUndefined();
  });

  it("emits dismiss when Done is clicked after acknowledging", async () => {
    const wrapper = mount(RevealModal, { props: { token: "pk_live_secret" } });
    await wrapper.get('[data-testid="reveal-ack"]').setValue(true);
    await wrapper.get('[data-testid="reveal-dismiss"]').trigger("click");
    expect(wrapper.emitted("dismiss")).toHaveLength(1);
  });
});
