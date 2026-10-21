import { computed, ref } from "vue";

export type Appearance = "system" | "light" | "dark";
const KEY = "whisper-ditado:appearance";
const preference = ref<Appearance>("system");
const systemDark = ref(false);
let initialized = false;
function valid(value: string | null): Appearance {
  return value === "light" || value === "dark" ? value : "system";
}
function apply(): void {
  document.documentElement.dataset.theme =
    preference.value === "system"
      ? systemDark.value
        ? "dark"
        : "light"
      : preference.value;
}
export function initAppearance(): void {
  if (initialized || typeof window === "undefined") return;
  initialized = true;
  try {
    preference.value = valid(localStorage.getItem(KEY));
  } catch {
    /* armazenamento indisponível */
  }
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  systemDark.value = media?.matches ?? false;
  media?.addEventListener("change", (event) => {
    systemDark.value = event.matches;
    apply();
  });
  window.addEventListener("storage", (event) => {
    if (event.key === KEY) {
      preference.value = valid(event.newValue);
      apply();
    }
  });
  apply();
}
export function useAppearance() {
  initAppearance();
  return {
    preference: computed(() => preference.value),
    theme: computed(() =>
      preference.value === "system"
        ? systemDark.value
          ? "dark"
          : "light"
        : preference.value,
    ),
    setAppearance(value: Appearance) {
      preference.value = value;
      try {
        localStorage.setItem(KEY, value);
      } catch {
        /* mantém a preferência nesta janela */
      }
      apply();
    },
  };
}
