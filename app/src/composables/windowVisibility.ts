// A janela está à vista? Fechar a janela principal só a esconde (o app fica na bandeja) e nem
// sempre o WebKitGTK marca `document.hidden` nesse caso; por isso o Rust também avisa pelo evento
// `main-visibility` (true ao mostrar, false ao esconder). Visível = os dois dizem que sim.
import { listen } from "@tauri-apps/api/event";

export const MAIN_VISIBILITY_EVENT = "main-visibility";

/** Página visível agora (sem contar o aviso do Rust, que só chega nas mudanças). */
export function pageVisible(): boolean {
  return document.visibilityState !== "hidden";
}

/**
 * Chama `cb(visível)` a cada mudança (sem repetir o mesmo valor). Parte de "visível segundo o
 * Rust": quem assina é uma tela que o usuário acabou de abrir. Devolve a função que desassina.
 */
export function onWindowVisibility(cb: (visible: boolean) => void): () => void {
  let shown = true;
  let last = pageVisible();
  let stopped = false;
  let unlisten: (() => void) | null = null;

  const update = () => {
    const next = shown && pageVisible();
    if (next === last) return;
    last = next;
    cb(next);
  };
  document.addEventListener("visibilitychange", update);
  listen<boolean>(MAIN_VISIBILITY_EVENT, (e) => {
    shown = e.payload !== false;
    update();
  })
    .then((off) => {
      if (stopped) off();
      else unlisten = off;
    })
    .catch(() => {}); // fora do Tauri (testes sem mock): fica só o `visibilitychange`

  return () => {
    stopped = true;
    document.removeEventListener("visibilitychange", update);
    unlisten?.();
  };
}
