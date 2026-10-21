import { describe, expect, it, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";
import type { ApiClient } from "../../api/client";
import type { Settings } from "../../api/types";
import { createSettingsForm } from "./useSettingsForm";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

const snapshot = (values: Partial<Settings> = {}): Settings =>
  ({ dictation_enabled: true, trailing_space: true, ...values }) as Settings;

describe("settings request ordering", () => {
  it("serializes edits to different fields so an older snapshot cannot overwrite them", async () => {
    const first = deferred<Settings>();
    const second = deferred<Settings>();
    const putSettings = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const form = createSettingsForm({ putSettings } as unknown as ApiClient);
    const a = form.save("trailing_space", false);
    const b = form.save("dictation_enabled", false);
    await flushPromises();
    expect(putSettings).toHaveBeenCalledTimes(1);
    expect(form.saving.dictation_enabled).toBe(true);
    first.resolve(snapshot({ trailing_space: false }));
    await a;
    await flushPromises();
    expect(putSettings).toHaveBeenNthCalledWith(2, { dictation_enabled: false });
    second.resolve(snapshot({ trailing_space: false, dictation_enabled: false }));
    await b;
    expect(form.settings.value?.dictation_enabled).toBe(false);
    expect(form.settings.value?.trailing_space).toBe(false);
    expect(form.saving.dictation_enabled).toBe(false);
  });

  it("keeps a field busy until its last queued edit completes", async () => {
    const first = deferred<Settings>();
    const second = deferred<Settings>();
    const putSettings = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const form = createSettingsForm({ putSettings } as unknown as ApiClient);
    const a = form.save("dictation_enabled", false);
    const b = form.save("dictation_enabled", true);
    await flushPromises();
    first.resolve(snapshot({ dictation_enabled: false }));
    await a;
    expect(form.saving.dictation_enabled).toBe(true);
    await flushPromises();
    expect(putSettings).toHaveBeenNthCalledWith(2, { dictation_enabled: true });
    second.resolve(snapshot());
    await b;
    expect(form.settings.value?.dictation_enabled).toBe(true);
    expect(form.saving.dictation_enabled).toBe(false);
  });

  it("continues saving after a failure and clears the previous field error", async () => {
    const putSettings = vi.fn().mockRejectedValueOnce(new Error("falhou")).mockResolvedValueOnce(snapshot());
    const form = createSettingsForm({ putSettings } as unknown as ApiClient);
    expect(await form.save("dictation_enabled", false)).toBe(false);
    expect(form.errors.dictation_enabled).toBe("falhou");
    expect(await form.save("dictation_enabled", true)).toBe(true);
    expect(form.errors.dictation_enabled).toBeUndefined();
    expect(form.saving.dictation_enabled).toBe(false);
  });

  it("finishes an older read before starting a save", async () => {
    const read = deferred<Settings>();
    const getSettings = vi.fn(() => read.promise);
    const putSettings = vi.fn(async () => snapshot({ trailing_space: false }));
    const form = createSettingsForm({ getSettings, putSettings } as unknown as ApiClient);
    const loading = form.load();
    const saving = form.save("trailing_space", false);
    await flushPromises();
    expect(putSettings).not.toHaveBeenCalled();
    read.resolve(snapshot());
    await loading;
    await saving;
    expect(form.settings.value?.trailing_space).toBe(false);
  });
});
