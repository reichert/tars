import { nextTick, onBeforeUnmount, onMounted, type Ref } from "vue";

const focusable =
  'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
export function useFocusScope(
  root: Ref<HTMLElement | null>,
  trap = true,
  initialFocus?: () => HTMLElement | null | undefined,
) {
  let previous: HTMLElement | null = null;
  function keydown(event: KeyboardEvent) {
    if (!trap || event.key !== "Tab" || !root.value) return;
    const controls = Array.from(
      root.value.querySelectorAll<HTMLElement>(focusable),
    ).filter(
      (el) =>
        !el.closest("[hidden], [inert]") &&
        getComputedStyle(el).display !== "none",
    );
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (!first) {
      event.preventDefault();
      root.value.focus();
      return;
    }
    if (
      event.shiftKey &&
      (document.activeElement === first ||
        document.activeElement === root.value)
    ) {
      event.preventDefault();
      last?.focus();
    } else if (
      !event.shiftKey &&
      (document.activeElement === last || document.activeElement === root.value)
    ) {
      event.preventDefault();
      first.focus();
    }
  }
  onMounted(async () => {
    previous = document.activeElement as HTMLElement;
    await nextTick();
    (initialFocus?.() ?? root.value)?.focus();
    root.value?.addEventListener("keydown", keydown);
  });
  onBeforeUnmount(() => {
    root.value?.removeEventListener("keydown", keydown);
    if (previous?.isConnected) previous.focus();
  });
}
