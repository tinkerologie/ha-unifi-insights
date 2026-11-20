import { backoffDelay } from "../data/subscription";
import {
  toWsError,
  type Connection,
  type UnsubscribeFunc,
  type WsError,
} from "../ha-types";
import {
  ERR_ENTRY_NOT_LOADED,
  ERR_GROUPING_DISABLED,
  IncompatibleDeviceGroupsSchemaError,
  WS_DEVICE_GROUPS_SUBSCRIBE,
  parseDeviceGroupsSnapshot,
  type DeviceGroupsSnapshot,
} from "./contract";

export interface DeviceGroupsSubscriptionHandlers {
  onSnapshot(snapshot: DeviceGroupsSnapshot): void;
  onError(error: WsError): void;
  onGroupingDisabled(entryId: string): void;
  onIncompatible(): void;
  /** The socket dropped: whatever is drawn is no longer live. */
  onDisconnected(): void;
  /** The socket is back; the stream (if any) has been reopened. */
  onReconnected(): void;
}

/**
 * Errors that mean "not yet": the entry is still setting up, or (right after
 * an HA restart) the integration has not registered its commands.
 */
const RETRYABLE = new Set([ERR_ENTRY_NOT_LOADED, "unknown_command"]);

/**
 * One panel's `device_groups/subscribe` stream.
 *
 * Modeled on `TopologySubscription`: manages socket reconnects through
 * `ready`/`disconnected` with `resubscribe: false`, retries transient
 * `entry_not_loaded` / `unknown_command` / `entry_unloaded` with backoff,
 * treats `grouping_disabled` (error or status) as terminal, and guards every
 * callback with a generation counter.
 */
export class DeviceGroupsSubscription {
  private generation = 0;
  private connection: Connection | undefined;
  private entryId: string | undefined;
  private unsubscribe: UnsubscribeFunc | undefined;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private attempt = 0;
  private lastRevision: string | undefined;
  private isLive = false;
  private terminalDisabled = false;
  private readonly handlers: DeviceGroupsSubscriptionHandlers;
  private readonly random: () => number;

  constructor(
    handlers: DeviceGroupsSubscriptionHandlers,
    random: () => number = Math.random,
  ) {
    this.handlers = handlers;
    this.random = random;
  }

  get live(): boolean {
    return this.isLive;
  }

  /** Point the stream at a connection and entry; a no-op when neither changed. */
  update(
    connection: Connection | undefined,
    entryId: string | undefined,
  ): void {
    if (connection === this.connection && entryId === this.entryId) return;
    this.stop();
    this.connection = connection;
    this.entryId = entryId;
    if (connection) {
      connection.addEventListener("ready", this.onReady);
      connection.addEventListener("disconnected", this.onDisconnected);
    }
    if (connection && entryId) this.open();
  }

  stop(): void {
    this.generation++;
    this.clearRetry();
    this.attempt = 0;
    this.terminalDisabled = false;
    this.release();
    this.connection?.removeEventListener("ready", this.onReady);
    this.connection?.removeEventListener("disconnected", this.onDisconnected);
    this.connection = undefined;
    this.entryId = undefined;
  }

  private clearRetry(): void {
    if (this.retryTimer !== undefined) clearTimeout(this.retryTimer);
    this.retryTimer = undefined;
  }

  /** The socket is gone and took the subscription with it: nothing to unsubscribe. */
  private drop(): void {
    this.generation++;
    this.clearRetry();
    this.unsubscribe = undefined;
    this.isLive = false;
    this.lastRevision = undefined;
  }

  private readonly onDisconnected = (): void => {
    this.drop();
    this.handlers.onDisconnected();
  };

  private readonly onReady = (): void => {
    this.drop();
    this.attempt = 0;
    if (this.entryId && !this.terminalDisabled) this.open();
    this.handlers.onReconnected();
  };

  private release(): void {
    const unsubscribe = this.unsubscribe;
    this.unsubscribe = undefined;
    this.isLive = false;
    this.lastRevision = undefined;
    if (unsubscribe) unsubscribe().catch(() => undefined);
  }

  private open(): void {
    const generation = ++this.generation;
    const { connection, entryId } = this;
    if (!connection || !entryId) return;
    connection
      .subscribeMessage<unknown>(
        (raw) => {
          if (generation === this.generation) this.receive(raw);
        },
        {
          type: WS_DEVICE_GROUPS_SUBSCRIBE,
          entry_id: entryId,
        },
        { resubscribe: false },
      )
      .then(
        (unsubscribe) => {
          if (generation === this.generation) this.unsubscribe = unsubscribe;
          else unsubscribe().catch(() => undefined);
        },
        (err: unknown) => {
          if (generation !== this.generation) return;
          const error = toWsError(err);
          this.handlers.onError(error);
          if (error.code === ERR_GROUPING_DISABLED) {
            this.generation++;
            this.terminalDisabled = true;
            this.clearRetry();
            this.release();
            this.handlers.onGroupingDisabled(entryId);
            return;
          }
          if (RETRYABLE.has(error.code)) this.scheduleRetry();
        },
      );
  }

  private receive(raw: unknown): void {
    let snapshot: DeviceGroupsSnapshot;
    try {
      snapshot = parseDeviceGroupsSnapshot(raw);
    } catch (err) {
      if (err instanceof IncompatibleDeviceGroupsSchemaError) {
        this.generation++;
        this.clearRetry();
        this.release();
        this.handlers.onIncompatible();
      } else {
        this.handlers.onError({
          code: "invalid_payload",
          message: String(err),
        });
      }
      return;
    }
    const unloaded = snapshot.status === "entry_unloaded";
    const disabled = snapshot.status === "grouping_disabled";
    if (!unloaded && !disabled) {
      // Set here, not after `await subscribeMessage`: HA can deliver the
      // result and the first event in the same frame.
      this.isLive = true;
      this.attempt = 0;
    }
    if (snapshot.revision !== "" && snapshot.revision === this.lastRevision)
      return;
    this.lastRevision = snapshot.revision;
    this.handlers.onSnapshot(snapshot);
    if (unloaded) {
      this.generation++;
      this.release();
      this.scheduleRetry();
    } else if (disabled) {
      this.generation++;
      this.terminalDisabled = true;
      this.clearRetry();
      this.release();
      this.handlers.onGroupingDisabled(snapshot.entry_id);
    }
  }

  private scheduleRetry(): void {
    const generation = this.generation;
    const delay = backoffDelay(this.attempt++, this.random);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = undefined;
      if (generation === this.generation) this.open();
    }, delay);
  }
}
