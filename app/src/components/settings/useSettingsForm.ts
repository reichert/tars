// Estado compartilhado das seções de configuração: valores atuais, salvamento campo a campo e o
// erro de validação do backend por campo. Cada campo vai num `PUT /settings` só dele, então a
// mensagem de erro (o backend devolve um `detail` único) sempre pertence ao campo enviado.
import { reactive, ref, type Ref } from "vue";
import type { ApiClient } from "../../api/client";
import type { Settings, SettingsUpdate } from "../../api/types";

export type SettingKey = keyof SettingsUpdate;

export interface SettingsForm {
  client: ApiClient;
  /** Últimos valores confirmados pelo backend (null até carregar). */
  settings: Ref<Settings | null>;
  loadError: Ref<string | null>;
  /** Mensagem de erro por campo, enquanto o último salvamento dele falhou. */
  errors: Record<string, string>;
  /** Campos com `PUT` em andamento. */
  saving: Record<string, boolean>;
  load(): Promise<void>;
  /** Salva um campo; devolve `false` (e preenche `errors[key]`) se o backend recusar. */
  save<K extends SettingKey>(key: K, value: SettingsUpdate[K]): Promise<boolean>;
  clearError(key: SettingKey): void;
}

export function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function createSettingsForm(client: ApiClient): SettingsForm {
  const settings = ref<Settings | null>(null);
  const loadError = ref<string | null>(null);
  const errors = reactive<Record<string, string>>({});
  const saving = reactive<Record<string, boolean>>({});
  const pending = new Map<string, number>();
  let tail: Promise<unknown> = Promise.resolve();

  // PUT devolve todas as configurações: serializar também as leituras evita que uma resposta
  // antiga substitua um salvamento mais recente, inclusive ao editar o mesmo campo duas vezes.
  function enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = tail.then(operation);
    tail = result.catch(() => {});
    return result;
  }

  async function load(): Promise<void> {
    await enqueue(async () => {
      loadError.value = null;
      try {
        settings.value = await client.getSettings();
      } catch (e) {
        loadError.value = messageOf(e);
      }
    });
  }

  async function save<K extends SettingKey>(key: K, value: SettingsUpdate[K]): Promise<boolean> {
    delete errors[key];
    pending.set(key, (pending.get(key) ?? 0) + 1);
    saving[key] = true;
    return enqueue(async () => {
      delete errors[key];
      try {
        settings.value = await client.putSettings({ [key]: value } as SettingsUpdate);
        return true;
      } catch (e) {
        errors[key] = messageOf(e);
        return false;
      } finally {
        const remaining = (pending.get(key) ?? 1) - 1;
        if (remaining) pending.set(key, remaining);
        else pending.delete(key);
        saving[key] = remaining > 0;
      }
    });
  }

  function clearError(key: SettingKey): void {
    delete errors[key];
  }

  return { client, settings, loadError, errors, saving, load, save, clearError };
}
