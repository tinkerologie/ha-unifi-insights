import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BACKOFF_MAX_MS,
  TopologySubscription,
  backoffDelay,
} from "../src/data/subscription";
import type { Connection, MessageBase } from "../src/ha-types";
import { fixtureSnapshot, unavailableSnapshot } from "./helpers";

interface Call {
  callback: (message: unknown) => void;
  message: MessageBase;
  options: unknown;
  unsubscribe: ReturnType<typeof vi.fn>;
}

function fakeConnection(opts: { emitOnSubscribe?: unknown } = {}) {
  const calls: Call[] = [];
  let failNext: unknown;
  let hold: ((u: () => Promise<void>) => void)[] | undefined;
  const listeners = new Map<string, Set<() => void>>();
  const connection = {
    addEventListener: vi.fn((event: string, listener: () => void) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)!.add(listener);
    }),
    removeEventListener: vi.fn((event: string, listener: () => void) => {
      listeners.get(event)?.delete(listener);
    }),
    subscribeMessage: vi.fn(
      (
        callback: (m: unknown) => void,
        message: MessageBase,
        options?: unknown,
      ) => {
        if (failNext !== undefined) {
          const err = failNext;
          failNext = undefined;
          return Promise.reject(err);
        }
        const unsubscribe = vi.fn(async () => undefined);
        calls.push({ callback, message, options, unsubscribe });
        // HA can deliver the first event in the same frame as the result.
        if (opts.emitOnSubscribe !== undefined) callback(opts.emitOnSubscribe);
        if (hold)
          return new Promise<() => Promise<void>>((resolve) =>
            hold!.push(resolve),
          ).then(() => unsubscribe);
        return Promise.resolve(unsubscribe);
      },
    ),
  };
  return {
    connection: connection as unknown as Connection,
    calls,
    emit: (event: "ready" | "disconnected") =>
      listeners.get(event)?.forEach((listener) => listener()),
    listenerCount: () =>
      [...listeners.values()].reduce((n, set) => n + set.size, 0),
    failNext: (err: unknown) => {
      failNext = err;
    },
    holdResults: () => {
      hold = [];
      return () => hold!.splice(0).forEach((r) => r(async () => undefined));
    },
  };
}

const KEY = { entry_id: "entry-1", site_id: "site-1", max_clients: 500 };
const flush = () => new Promise((r) => setTimeout(r, 0));

function handlers() {
  return {
    onSnapshot: vi.fn(),
    onError: vi.fn(),
    onIncompatible: vi.fn(),
    onDisconnected: vi.fn(),
    onReconnected: vi.fn(),
  };
}

describe("TopologySubscription", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("subscribes once with the site key and no library resubscribe", async () => {
    const fake = fakeConnection();
    const sub = new TopologySubscription(handlers());
    sub.update(fake.connection, KEY);
    sub.update(fake.connection, { ...KEY });
    await vi.runAllTimersAsync();
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0]!.message).toEqual({
      type: "unifi_insights/topology/subscribe",
      ...KEY,
    });
    expect(fake.calls[0]!.options).toEqual({ resubscribe: false });
  });

  it("resubscribes when the key or the connection changes", async () => {
    const fake = fakeConnection();
    const other = fakeConnection();
    const sub = new TopologySubscription(handlers());
    sub.update(fake.connection, KEY);
    await vi.runAllTimersAsync();
    sub.update(fake.connection, { ...KEY, site_id: "site-2" });
    await vi.runAllTimersAsync();
    expect(fake.calls[0]!.unsubscribe).toHaveBeenCalledOnce();
    expect(fake.calls).toHaveLength(2);
    sub.update(other.connection, { ...KEY, site_id: "site-2" });
    await vi.runAllTimersAsync();
    expect(fake.calls[1]!.unsubscribe).toHaveBeenCalledOnce();
    expect(other.calls).toHaveLength(1);
  });

  it("marks live inside the event callback, before the result resolves", () => {
    const fake = fakeConnection({ emitOnSubscribe: fixtureSnapshot() });
    const h = handlers();
    const sub = new TopologySubscription(h);
    sub.update(fake.connection, KEY);
    expect(sub.live).toBe(true);
    expect(h.onSnapshot).toHaveBeenCalledOnce();
  });

  it("drops a repeated revision", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new TopologySubscription(h);
    sub.update(fake.connection, KEY);
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback(fixtureSnapshot());
    fake.calls[0]!.callback(fixtureSnapshot());
    expect(h.onSnapshot).toHaveBeenCalledOnce();
  });

  it("delivers consecutive unavailable snapshots with empty revisions", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new TopologySubscription(h);
    sub.update(fake.connection, KEY);
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback(unavailableSnapshot("site_unavailable"));
    fake.calls[0]!.callback(unavailableSnapshot("entry_unloaded"));
    expect(h.onSnapshot).toHaveBeenCalledTimes(2);
    expect(h.onSnapshot.mock.calls[1]![0].issues[0].code).toBe(
      "entry_unloaded",
    );
  });

  it("releases a server-ended stream and retries with backoff until it recovers", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new TopologySubscription(h, () => 0.5); // jitter factor exactly 1.0
    sub.update(fake.connection, KEY);
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback(unavailableSnapshot("entry_unloaded"));
    expect(fake.calls[0]!.unsubscribe).toHaveBeenCalledOnce();
    expect(sub.live).toBe(false);

    fake.failNext({ code: "entry_not_loaded", message: "not loaded" });
    await vi.advanceTimersByTimeAsync(1999);
    expect(fake.connection.subscribeMessage).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fake.connection.subscribeMessage).toHaveBeenCalledTimes(2);
    expect(h.onError).toHaveBeenCalledWith({
      code: "entry_not_loaded",
      message: "not loaded",
    });

    await vi.advanceTimersByTimeAsync(4000); // second retry: 4 s
    expect(fake.calls).toHaveLength(2);
    fake.calls[1]!.callback(fixtureSnapshot());
    expect(sub.live).toBe(true);

    // Backoff reset: the next unload waits 2 s again, not 8 s.
    fake.calls[1]!.callback(unavailableSnapshot("entry_unloaded"));
    await vi.advanceTimersByTimeAsync(2000);
    expect(fake.calls).toHaveLength(3);
  });

  it("reopens the stream itself when the socket comes back", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new TopologySubscription(h);
    sub.update(fake.connection, KEY);
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback(fixtureSnapshot());
    expect(sub.live).toBe(true);

    fake.emit("disconnected");
    expect(h.onDisconnected).toHaveBeenCalledOnce();
    expect(sub.live).toBe(false);
    // The socket took the subscription with it: nothing to unsubscribe.
    expect(fake.calls[0]!.unsubscribe).not.toHaveBeenCalled();
    fake.calls[0]!.callback(fixtureSnapshot());
    expect(h.onSnapshot).toHaveBeenCalledOnce();

    fake.emit("ready");
    await vi.runAllTimersAsync();
    expect(h.onReconnected).toHaveBeenCalledOnce();
    expect(fake.calls).toHaveLength(2);
    // Same revision as before the drop: still delivered, it is a fresh stream.
    fake.calls[1]!.callback(fixtureSnapshot());
    expect(h.onSnapshot).toHaveBeenCalledTimes(2);
    expect(sub.live).toBe(true);
  });

  it("retries a reopen that fails while HA is still starting", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new TopologySubscription(h, () => 0.5);
    sub.update(fake.connection, KEY);
    await vi.runAllTimersAsync();
    fake.emit("disconnected");
    fake.failNext({ code: "unknown_command", message: "Unknown command." });
    fake.emit("ready");
    await vi.advanceTimersByTimeAsync(0);
    expect(h.onError).toHaveBeenCalledWith({
      code: "unknown_command",
      message: "Unknown command.",
    });
    await vi.advanceTimersByTimeAsync(2000);
    expect(fake.connection.subscribeMessage).toHaveBeenCalledTimes(3);
    expect(fake.calls).toHaveLength(2);
  });

  it("stops listening to a connection it no longer uses", () => {
    const fake = fakeConnection();
    const other = fakeConnection();
    const sub = new TopologySubscription(handlers());
    sub.update(fake.connection, KEY);
    expect(fake.listenerCount()).toBe(2);
    sub.update(other.connection, KEY);
    expect(fake.listenerCount()).toBe(0);
    expect(other.listenerCount()).toBe(2);
    sub.stop();
    expect(other.listenerCount()).toBe(0);
  });

  it("does not retry errors a retry cannot fix", async () => {
    const fake = fakeConnection();
    const h = handlers();
    fake.failNext({ code: "site_not_selected", message: "no" });
    new TopologySubscription(h).update(fake.connection, KEY);
    await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS * 2);
    expect(h.onError).toHaveBeenCalledOnce();
    expect(fake.connection.subscribeMessage).toHaveBeenCalledOnce();
  });

  it("stop cancels a pending retry and ignores late events", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new TopologySubscription(h);
    sub.update(fake.connection, KEY);
    await vi.runAllTimersAsync();
    const stale = fake.calls[0]!.callback;
    stale(unavailableSnapshot("entry_unloaded"));
    sub.stop();
    await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS * 2);
    expect(fake.connection.subscribeMessage).toHaveBeenCalledOnce();
    stale(fixtureSnapshot());
    expect(h.onSnapshot).toHaveBeenCalledOnce();
  });

  it("unsubscribes a subscription that resolves after stop", async () => {
    vi.useRealTimers();
    const fake = fakeConnection();
    const release = fake.holdResults();
    const sub = new TopologySubscription(handlers());
    sub.update(fake.connection, KEY);
    sub.stop();
    sub.update(fake.connection, KEY); // re-attached before the first result arrived
    release();
    await flush();
    expect(fake.calls).toHaveLength(2);
    expect(fake.calls[0]!.unsubscribe).toHaveBeenCalledOnce();
    expect(fake.calls[1]!.unsubscribe).not.toHaveBeenCalled();
  });

  it("reports an incompatible schema and a malformed payload", async () => {
    const fake = fakeConnection();
    const h = handlers();
    new TopologySubscription(h).update(fake.connection, KEY);
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback({ ...fixtureSnapshot(), schema_version: 1 });
    fake.calls[0]!.callback({ nope: true });
    expect(h.onIncompatible).toHaveBeenCalledOnce();
    expect(h.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "invalid_payload" }),
    );
  });

  it("does nothing without a connection or a key", () => {
    const fake = fakeConnection();
    const sub = new TopologySubscription(handlers());
    sub.update(undefined, KEY);
    sub.update(fake.connection, undefined);
    expect(fake.connection.subscribeMessage).not.toHaveBeenCalled();
  });
});

describe("backoffDelay", () => {
  it("doubles from 2 s, caps at 60 s and jitters ±20 %", () => {
    expect(backoffDelay(0, () => 0.5)).toBe(2000);
    expect(backoffDelay(1, () => 0.5)).toBe(4000);
    expect(backoffDelay(10, () => 0.5)).toBe(60000);
    expect(backoffDelay(0, () => 0)).toBe(1600);
    expect(backoffDelay(0, () => 1)).toBe(2400);
  });
});
