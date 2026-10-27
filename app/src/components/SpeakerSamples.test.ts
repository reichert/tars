import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import type { Segment, Speaker } from "../api/types";
import { setStatusStore } from "../composables/useStatus";
import { installStore } from "../test/meetingFakes";
import SpeakerSamples from "./SpeakerSamples.vue";

const speaker: Speaker = { id: "s2", label: "Pessoa 2", voice_id: null, is_me: false, color_index: 1 };
const segments: Segment[] = [
  { id: 1, speaker_id: "s1", source: "mic", start_s: 0, end_s: 10, text: "Outra pessoa", dictated: false },
  { id: 2, speaker_id: "s2", source: "pc", start_s: 31, end_s: 45, text: "Primeira fala", dictated: false },
  { id: 3, speaker_id: "s2", source: "pc", start_s: 60, end_s: 65, text: "Outra fala", dictated: false },
];

let wrapper: VueWrapper | undefined;
const createObjectURL = vi.fn(() => "blob:sample");
const revokeObjectURL = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(URL, { createObjectURL, revokeObjectURL });
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  setStatusStore(null);
});

function setup(audioBlob = vi.fn(async (..._args: unknown[]) => new Blob(["audio"]))) {
  installStore({ audioBlob });
  wrapper = mount(SpeakerSamples, { props: { meetingId: "m1", speaker, segments } });
  return { w: wrapper, audioBlob };
}

describe("SpeakerSamples", () => {
  it("só busca o áudio ao clicar e toca um trecho limitado da trilha da pessoa", async () => {
    const { w, audioBlob } = setup();
    expect(audioBlob).not.toHaveBeenCalled();
    expect(w.findAll("button")).toHaveLength(2);
    expect(w.get("button").attributes("aria-label")).toContain("Pessoa 2 em 00:31");
    await w.get("button").trigger("click");
    await flushPromises();
    expect(audioBlob).toHaveBeenCalledWith("m1", 31, 41, "pc");
    expect(w.get("audio").attributes("src")).toBe("blob:sample");
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
    expect(w.emitted("play")).toHaveLength(1);
  });

  it("mostra erro e permite tentar novamente", async () => {
    const audioBlob = vi.fn().mockRejectedValueOnce(new Error("Áudio não encontrado"))
      .mockResolvedValue(new Blob(["audio"]));
    const { w } = setup(audioBlob);
    await w.get("button").trigger("click");
    await flushPromises();
    expect(w.get('[role="alert"]').text()).toBe("Áudio não encontrado");
    expect(w.get("button").attributes("disabled")).toBeUndefined();
    await w.get("button").trigger("click");
    await flushPromises();
    expect(w.find('[role="alert"]').exists()).toBe(false);
    expect(w.find("audio").exists()).toBe(true);
  });

  it("descarta uma resposta antiga ao escolher outro trecho", async () => {
    let finishFirst!: (blob: Blob) => void;
    const audioBlob = vi.fn().mockImplementationOnce(() => new Promise<Blob>((r) => { finishFirst = r; }))
      .mockResolvedValueOnce(new Blob(["novo"]));
    const { w } = setup(audioBlob);
    await w.findAll("button")[0].trigger("click");
    expect(w.get('[role="status"]').text()).toContain("Carregando");
    await w.findAll("button")[1].trigger("click");
    await flushPromises();
    finishFirst(new Blob(["antigo"]));
    await flushPromises();
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(audioBlob).toHaveBeenLastCalledWith("m1", 60, 65, "pc");
  });

  it("para e libera o áudio ao fechar o menu", async () => {
    const { w } = setup();
    await w.get("button").trigger("click");
    await flushPromises();
    w.unmount();
    wrapper = undefined;
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:sample");
  });

  it("ignora downloads que terminam depois de fechar o menu", async () => {
    let finish!: (blob: Blob) => void;
    const { w } = setup(vi.fn(() => new Promise<Blob>((r) => { finish = r; })));
    await w.get("button").trigger("click");
    w.unmount();
    wrapper = undefined;
    finish(new Blob(["audio"]));
    await flushPromises();
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
  });

  it("descarta o áudio ao trocar de pessoa", async () => {
    const { w } = setup();
    await w.get("button").trigger("click");
    await flushPromises();
    await w.setProps({ speaker: { ...speaker, id: "s3", label: "Pessoa 3" } });
    expect(w.find("audio").exists()).toBe(false);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:sample");
    expect(w.text()).toContain("Nenhum trecho disponível");
  });
});
