import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BACKOFF_MAX_MS } from "../src/data/subscription";
import type { Connection, MessageBase } from "../src/ha-types";
import type { DeviceGroupsSnapshot } from "../src/panel/contract";
import { DeviceGroupsSubscription } from "../src/panel/subscription";

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

function makeSnapshot(
  overrides: Partial<DeviceGroupsSnapshot> = {},
): DeviceGroupsSnapshot {
  return {
    schema_version: 1,
    entry_id: "entry-1",
    status: "ok",
    revision: "rev-1",
    stale: false,
    sections: [],
    ...overrides,
  };
}

function handlers() {
  return {
    onSnapshot: vi.fn(),
    onError: vi.fn(),
    onGroupingDisabled: vi.fn(),
    onIncompatible: vi.fn(),
    onDisconnected: vi.fn(),
    onReconnected: vi.fn(),
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("DeviceGroupsSubscription", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("subscribes once per entry_id with resubscribe false", async () => {
    const fake = fakeConnection();
    const sub = new DeviceGroupsSubscription(handlers());
    sub.update(fake.connection, "entry-1");
    sub.update(fake.connection, "entry-1");
    await vi.runAllTimersAsync();
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0]!.message).toEqual({
      type: "unifi_insights/device_groups/subscribe",
      entry_id: "entry-1",
    });
    expect(fake.calls[0]!.options).toEqual({ resubscribe: false });
  });

  it("resubscribes when entry_id or connection changes", async () => {
    const fake = fakeConnection();
    const other = fakeConnection();
    const sub = new DeviceGroupsSubscription(handlers());
    sub.update(fake.connection, "entry-1");
    await vi.runAllTimersAsync();
    sub.update(fake.connection, "entry-2");
    await vi.runAllTimersAsync();
    expect(fake.calls[0]!.unsubscribe).toHaveBeenCalledOnce();
    expect(fake.calls).toHaveLength(2);
    sub.update(other.connection, "entry-2");
    await vi.runAllTimersAsync();
    expect(fake.calls[1]!.unsubscribe).toHaveBeenCalledOnce();
    expect(other.calls).toHaveLength(1);
  });

  it("marks live inside the event callback before subscribe resolves", () => {
    const fake = fakeConnection({ emitOnSubscribe: makeSnapshot() });
    const h = handlers();
    const sub = new DeviceGroupsSubscription(h);
    sub.update(fake.connection, "entry-1");
    expect(sub.live).toBe(true);
    expect(h.onSnapshot).toHaveBeenCalledOnce();
  });

  it("dedupes repeated non-empty revisions and delivers empty revisions", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new DeviceGroupsSubscription(h);
    sub.update(fake.connection, "entry-1");
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback(makeSnapshot({ revision: "r1" }));
    fake.calls[0]!.callback(makeSnapshot({ revision: "r1" }));
    fake.calls[0]!.callback(makeSnapshot({ revision: "r2" }));
    expect(h.onSnapshot).toHaveBeenCalledTimes(2);
  });

  it("retries entry_unloaded, entry_not_loaded, and unknown_command with backoff", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new DeviceGroupsSubscription(h, () => 0.5);
    sub.update(fake.connection, "entry-1");
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback(
      makeSnapshot({ status: "entry_unloaded", revision: "" }),
    );
    expect(fake.calls[0]!.unsubscribe).toHaveBeenCalledOnce();
    expect(sub.live).toBe(false);

    fake.failNext({ code: "entry_not_loaded", message: "reloading" });
    await vi.advanceTimersByTimeAsync(2000);
    expect(h.onError).toHaveBeenCalledWith({
      code: "entry_not_loaded",
      message: "reloading",
    });

    fake.failNext({ code: "unknown_command", message: "starting" });
    await vi.advanceTimersByTimeAsync(4000);
    expect(h.onError).toHaveBeenCalledWith({
      code: "unknown_command",
      message: "starting",
    });

    await vi.advanceTimersByTimeAsync(8000);
    expect(fake.calls).toHaveLength(2);
    fake.calls[1]!.callback(makeSnapshot({ revision: "r1" }));
    expect(sub.live).toBe(true);
  });

  it("handles status grouping_disabled as terminal with no retry or ready reopen", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new DeviceGroupsSubscription(h);
    sub.update(fake.connection, "entry-1");
    await vi.runAllTimersAsync();

    fake.calls[0]!.callback(
      makeSnapshot({ status: "grouping_disabled", revision: "" }),
    );
    expect(fake.calls[0]!.unsubscribe).toHaveBeenCalledOnce();
    expect(sub.live).toBe(false);
    expect(h.onGroupingDisabled).toHaveBeenCalledWith("entry-1");

    await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS * 2);
    expect(fake.connection.subscribeMessage).toHaveBeenCalledTimes(1);

    // Even a socket ready event does not reopen a grouping_disabled stream.
    fake.emit("ready");
    await vi.runAllTimersAsync();
    expect(h.onReconnected).toHaveBeenCalledOnce();
    expect(fake.connection.subscribeMessage).toHaveBeenCalledTimes(1);
  });

  it("handles error code grouping_disabled as terminal with no retry", async () => {
    const fake = fakeConnection();
    const h = handlers();
    fake.failNext({ code: "grouping_disabled", message: "off" });
    const sub = new DeviceGroupsSubscription(h);
    sub.update(fake.connection, "entry-1");
    await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS * 2);

    expect(h.onError).toHaveBeenCalledWith({
      code: "grouping_disabled",
      message: "off",
    });
    expect(h.onGroupingDisabled).toHaveBeenCalledWith("entry-1");
    expect(fake.connection.subscribeMessage).toHaveBeenCalledOnce();
  });

  it("does not retry non-retryable errors such as entry_not_found", async () => {
    const fake = fakeConnection();
    const h = handlers();
    fake.failNext({ code: "entry_not_found", message: "missing" });
    new DeviceGroupsSubscription(h).update(fake.connection, "entry-1");
    await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS * 2);
    expect(h.onError).toHaveBeenCalledOnce();
    expect(h.onGroupingDisabled).not.toHaveBeenCalled();
    expect(fake.connection.subscribeMessage).toHaveBeenCalledOnce();
  });

  it("reopens stream on socket reconnect and ignores stale disconnected callbacks", async () => {
    const fake = fakeConnection();
    const h = handlers();
    const sub = new DeviceGroupsSubscription(h);
    sub.update(fake.connection, "entry-1");
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback(makeSnapshot({ revision: "r1" }));

    fake.emit("disconnected");
    expect(h.onDisconnected).toHaveBeenCalledOnce();
    expect(sub.live).toBe(false);
    expect(fake.calls[0]!.unsubscribe).not.toHaveBeenCalled();
    // Stale stream callback is a no-op.
    fake.calls[0]!.callback(makeSnapshot({ revision: "r2" }));
    expect(h.onSnapshot).toHaveBeenCalledOnce();

    fake.emit("ready");
    await vi.runAllTimersAsync();
    expect(h.onReconnected).toHaveBeenCalledOnce();
    expect(fake.calls).toHaveLength(2);
    // Same revision after reconnect is still delivered on the new stream.
    fake.calls[1]!.callback(makeSnapshot({ revision: "r1" }));
    expect(h.onSnapshot).toHaveBeenCalledTimes(2);
    expect(sub.live).toBe(true);
  });

  it("unsubscribes a pending subscribe that resolves after stop", async () => {
    vi.useRealTimers();
    const fake = fakeConnection();
    const release = fake.holdResults();
    const sub = new DeviceGroupsSubscription(handlers());
    sub.update(fake.connection, "entry-1");
    sub.stop();
    sub.update(fake.connection, "entry-1");
    release();
    await flush();
    expect(fake.calls).toHaveLength(2);
    expect(fake.calls[0]!.unsubscribe).toHaveBeenCalledOnce();
    expect(fake.calls[1]!.unsubscribe).not.toHaveBeenCalled();
  });

  it("reports incompatible schema and malformed payloads", async () => {
    const fake = fakeConnection();
    const h = handlers();
    new DeviceGroupsSubscription(h).update(fake.connection, "entry-1");
    await vi.runAllTimersAsync();
    fake.calls[0]!.callback({ nope: true });
    fake.calls[0]!.callback(makeSnapshot({ schema_version: 2 }));
    expect(h.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "invalid_payload" }),
    );
    expect(h.onIncompatible).toHaveBeenCalledOnce();
  });

  it("does nothing without a connection or entry_id", () => {
    const fake = fakeConnection();
    const sub = new DeviceGroupsSubscription(handlers());
    sub.update(undefined, "entry-1");
    sub.update(fake.connection, undefined);
    expect(fake.connection.subscribeMessage).not.toHaveBeenCalled();
  });
});
