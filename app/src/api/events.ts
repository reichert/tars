// WebSocket `/api/events` com reconexão e assinantes por tipo de evento.
//
// Cada tentativa relê `api.json` (o backend pode ter reiniciado com outro token/porta). O atraso
// entre tentativas começa em 0,5 s, dobra a cada falha até 5 s e volta a 0,5 s depois de abrir.
import { ref, type Ref } from "vue";
import type { ApiInfo, EventOf, ServerEvent, ServerEventType } from "./types";

export type ConnectionState = "connecting" | "open" | "closed";

export const MIN_DELAY_MS = 500;
export const MAX_DELAY_MS = 5000;

export interface EventConnection {
  /** Assina eventos de um tipo; devolve a função que cancela a assinatura. */
  on<T extends ServerEventType>(type: T, cb: (event: EventOf<T>) => void): () => void;
  state: Ref<ConnectionState>;
  /** Motivo da última falha ao conectar (ex.: "Serviço do ditado não está rodando"). */
  error: Ref<string | null>;
  /** Encerra a conexão e para de reconectar. */
  close(): void;
}

export function eventsUrl(info: ApiInfo): string {
  return `ws://127.0.0.1:${info.port}/api/events?token=${encodeURIComponent(info.token)}`;
}

export function connectEvents(
  getInfo: () => Promise<ApiInfo>,
  WebSocketImpl: typeof WebSocket = WebSocket,
): EventConnection {
  const state = ref<ConnectionState>("connecting");
  const error = ref<string | null>(null);
  const listeners = new Map<string, Set<(event: ServerEvent) => void>>();
  let ws: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let delay = MIN_DELAY_MS;
  let stopped = false;

  function dispatch(raw: unknown): void {
    let event: ServerEvent;
    try {
      event = JSON.parse(String(raw));
    } catch {
      return;
    }
    for (const cb of listeners.get(event?.type) ?? []) {
      try {
        cb(event);
      } catch (e) {
        console.error(`Falha no assinante do evento ${event.type}`, e); // não afeta os demais
      }
    }
  }

  function retry(): void {
    if (stopped || timer) return;
    state.value = "closed";
    timer = setTimeout(() => {
      timer = null;
      void attempt();
    }, delay);
    delay = Math.min(delay * 2, MAX_DELAY_MS);
  }

  async function attempt(): Promise<void> {
    state.value = "connecting";
    let info: ApiInfo;
    try {
      info = await getInfo();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      retry();
      return;
    }
    if (stopped) return;
    let sock: WebSocket;
    try {
      sock = new WebSocketImpl(eventsUrl(info));
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      retry();
      return;
    }
    ws = sock;
    sock.onopen = () => {
      if (ws !== sock) return;
      delay = MIN_DELAY_MS;
      error.value = null;
      state.value = "open";
    };
    sock.onmessage = (ev) => {
      if (ws === sock) dispatch(ev.data);
    };
    sock.onclose = (ev) => {
      if (ws !== sock) return;
      ws = null;
      if (ev?.code === 4401) error.value = "Token do serviço inválido";
      retry();
    };
    sock.onerror = () => {
      // o `close` vem logo depois e cuida da reconexão
    };
  }

  void attempt();

  return {
    on(type, cb) {
      let set = listeners.get(type);
      if (!set) listeners.set(type, (set = new Set()));
      const fn = cb as (event: ServerEvent) => void;
      set.add(fn);
      return () => {
        set.delete(fn);
      };
    },
    state,
    error,
    close() {
      stopped = true;
      if (timer) clearTimeout(timer);
      timer = null;
      const sock = ws;
      ws = null;
      sock?.close();
      state.value = "closed";
    },
  };
}
