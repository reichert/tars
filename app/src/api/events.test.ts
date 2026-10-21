import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectEvents } from "./events";
import type { ApiInfo } from "./types";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  serverClose(code = 1006): void {
    this.onclose?.({ code } as CloseEvent);
  }

  send(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

const flush = () => new Promise<void>((r) => queueMicrotask(r));
const last = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

describe("events", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reconnects and rereads api info after close", async () => {
    const infos: ApiInfo[] = [{ port: 1, token: "a" }, { port: 2, token: "b" },
                              { port: 3, token: "c" }];
    const getInfo = vi.fn(async () => infos.shift()!);
    const conn = connectEvents(getInfo, FakeWebSocket as unknown as typeof WebSocket);

    await flush();
    expect(conn.state.value).toBe("connecting");
    expect(last().url).toBe("ws://127.0.0.1:1/api/events?token=a");
    last().open();
    expect(conn.state.value).toBe("open");

    last().serverClose(4401);
    expect(conn.state.value).toBe("closed");
    expect(FakeWebSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(499);
    expect(FakeWebSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(getInfo).toHaveBeenCalledTimes(2);
    expect(last().url).toBe("ws://127.0.0.1:2/api/events?token=b");

    // falha sem abrir: o atraso dobra (0,5 s -> 1 s)
    last().serverClose();
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeWebSocket.instances).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(last().url).toBe("ws://127.0.0.1:3/api/events?token=c");

    conn.close();
    expect(last().closed).toBe(true);
  });

  it("events dispatches by type", async () => {
    const conn = connectEvents(async () => ({ port: 9, token: "t" }),
                               FakeWebSocket as unknown as typeof WebSocket);
    await flush();
    last().open();
    const levels = vi.fn();
    const alerts = vi.fn();
    const off = conn.on("levels", levels);
    conn.on("alert", alerts);

    last().send({ type: "levels", mic_db: -20, pc_db: -30 });
    last().send({ type: "alert", kind: "mic_silent", active: true, message: "Microfone sem sinal" });
    off();
    last().send({ type: "levels", mic_db: -10, pc_db: -10 });

    expect(levels).toHaveBeenCalledTimes(1);
    expect(levels).toHaveBeenCalledWith({ type: "levels", mic_db: -20, pc_db: -30 });
    expect(alerts).toHaveBeenCalledTimes(1);
    expect(alerts.mock.calls[0][0].message).toBe("Microfone sem sinal");
    conn.close();
  });
});
