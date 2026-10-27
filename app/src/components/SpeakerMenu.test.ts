import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ApiError } from "../api/client";
import type { Speaker, Voice } from "../api/types";
import { setStatusStore } from "../composables/useStatus";
import { installStore } from "../test/meetingFakes";
import SpeakerMenu from "./SpeakerMenu.vue";

const speakers: Speaker[] = [
  { id: "s1", label: "Você", voice_id: null, is_me: true, color_index: 0 },
  { id: "s2", label: "Pessoa 2", voice_id: null, is_me: false, color_index: 1 },
  { id: "s3", label: "Pessoa 3", voice_id: null, is_me: false, color_index: 2 },
];
const voices: Voice[] = [
  { id: "v1", name: "João", n_samples: 2, created_at: null, updated_at: null },
  { id: "v2", name: "Maria", n_samples: 1, created_at: null, updated_at: null },
];

afterEach(() => setStatusStore(null));

function mountMenu(overrides: Record<string, unknown> = {}) {
  const ctx = installStore({
    listVoices: vi.fn(async () => voices),
    renameSpeaker: vi.fn(async () => ({ id: "s2", label: "x" })),
    mergeSpeaker: vi.fn(async () => ({ id: "s3" })),
    enrollSpeaker: vi.fn(async () => voices[0]),
    ...overrides,
  });
  const w = mount(SpeakerMenu, { props: { meetingId: "m1", segments: [], speaker: speakers[1], speakers, x: 10, y: 20 } });
  const btn = (label: string) => w.findAll("button").find((b) => b.text().includes(label))!;
  return { w, btn, ...ctx };
}

describe("SpeakerMenu", () => {
  it("renames only in this meeting", async () => {
    const { w, btn, client } = mountMenu();
    await btn("Renomear nesta reunião").trigger("click");
    await w.get("input").setValue("  Carlos ");
    await w.get("form").trigger("submit");
    await flushPromises();
    expect(client.renameSpeaker).toHaveBeenCalledWith("s2", "Carlos");
    expect(client.enrollSpeaker).not.toHaveBeenCalled();
    expect(w.emitted("changed")).toHaveLength(1);
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("identifies as an existing voice", async () => {
    const { w, btn, client } = mountMenu();
    await btn("Identificar como").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("João");
    expect(w.text()).toContain("Maria");
    await btn("Maria").trigger("click");
    await flushPromises();
    expect(client.enrollSpeaker).toHaveBeenCalledWith("s2", { voice_id: "v2" });
    expect(w.emitted("changed")).toHaveLength(1);
  });

  it("identifies as a new voice by name", async () => {
    const { w, btn, client } = mountMenu();
    await btn("Identificar como").trigger("click");
    await flushPromises();
    await btn("Nova voz").trigger("click");
    await w.get("input").setValue("Ana");
    await w.get("form").trigger("submit");
    await flushPromises();
    expect(client.enrollSpeaker).toHaveBeenCalledWith("s2", { name: "Ana" });
  });

  it("merge calls API and refreshes", async () => {
    const { w, btn, client } = mountMenu();
    await btn("Juntar com").trigger("click");
    // só os outros falantes da reunião (não o próprio)
    const options = w.findAll(".option").map((o) => o.text());
    expect(options.some((t) => t.includes("Pessoa 2"))).toBe(false);
    await btn("Pessoa 3").trigger("click");
    await flushPromises();
    expect(client.mergeSpeaker).toHaveBeenCalledWith("s2", "s3");
    expect(w.emitted("changed")).toHaveLength(1);
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("shows the backend error and stays open", async () => {
    const enrollSpeaker = vi.fn(async () => {
      throw new ApiError(400, "Este falante não tem uma amostra de voz para cadastrar");
    });
    const { w, btn } = mountMenu({ enrollSpeaker });
    await btn("Identificar como").trigger("click");
    await flushPromises();
    await btn("João").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("não tem uma amostra de voz");
    expect(w.emitted("changed")).toBeUndefined();
    expect(w.emitted("close")).toBeUndefined();
  });

  it("closes on Escape and on backdrop click", async () => {
    const { w } = mountMenu();
    await w.get(".backdrop").trigger("click");
    expect(w.emitted("close")).toHaveLength(1);
    await w.get(".speaker-menu").trigger("keydown", { key: "Escape" });
    expect(w.emitted("close")).toHaveLength(2);
  });
});
