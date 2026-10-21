import { describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import SettingField from "./SettingField.vue";

describe("SettingField accessible relationships", () => {
  it("associates labels, hints and errors with a nested control and clears stale errors", async () => {
    const w = mount(SettingField, {
      props: {
        label: "Microfone",
        hint: "Escolha uma fonte",
        error: "Fonte indisponível",
      },
      slots: {
        default: '<div><select id="device"><option>USB</option></select></div>',
      },
    });
    await flushPromises();
    const select = w.get("select");
    expect(w.get("label").attributes("for")).toBe("device");
    const descriptions = select.attributes("aria-describedby")!.split(" ");
    expect(descriptions).toHaveLength(2);
    expect(descriptions).toContain(w.get('[role="alert"]').attributes("id"));
    expect(select.attributes("aria-invalid")).toBe("true");
    await w.setProps({ error: undefined });
    expect(select.attributes("aria-invalid")).toBeUndefined();
    expect(select.attributes("aria-describedby")!.split(" ")).toHaveLength(1);
  });
});
