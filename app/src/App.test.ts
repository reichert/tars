import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import App from "./App.vue";
import { installStore } from "./test/meetingFakes";
import { setStatusStore } from "./composables/useStatus";

let wrapper: ReturnType<typeof mount> | undefined;
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  setStatusStore(null);
  document.body.innerHTML = "";
});
function openApp() {
  const ctx = installStore();
  wrapper = mount(App, {
    attachTo: document.body,
    global: {
      stubs: {
        SettingsView: { template: "<div>Preferências</div>" },
        MeetingsView: { template: "<div>Reunião</div>" },
      },
    },
  });
  return ctx;
}
describe("Mineral shell", () => {
  it("moves the same capture control into settings without duplicating it", async () => {
    openApp();
    await flushPromises();
    expect(document.querySelectorAll(".rec")).toHaveLength(1);
    const control = document.querySelector(".rec");
    expect(document.querySelector("#capture-sidebar .rec")).toBe(control);
    await wrapper!.get(".settings-link").trigger("click");
    await flushPromises();
    expect(document.querySelector("#capture-global .rec")).toBe(control);
    expect(document.querySelectorAll(".rec")).toHaveLength(1);
    expect(wrapper!.get("aside").isVisible()).toBe(false);
  });
  it("keeps an in-flight start disabled after changing navigation", async () => {
    let finish!: (value: { meeting_id: string }) => void;
    const ctx = openApp();
    ctx.client.startMeeting = vi.fn(
      () =>
        new Promise<{ meeting_id: string }>((resolve) => {
          finish = resolve;
        }),
    );
    await flushPromises();
    await wrapper!.get(".rec").trigger("click");
    await wrapper!.get(".settings-link").trigger("click");
    await flushPromises();
    expect(
      document.querySelector<HTMLButtonElement>("#capture-global .rec")
        ?.disabled,
    ).toBe(true);
    expect(ctx.client.startMeeting).toHaveBeenCalledTimes(1);
    finish({ meeting_id: "m1" });
    await flushPromises();
  });
});
