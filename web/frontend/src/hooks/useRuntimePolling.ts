import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { EventBatch, RuntimeEvent, RuntimeStatus } from "../api/types";

export function useRuntimePolling(
  enabled: boolean,
  onEvents: (events: RuntimeEvent[]) => void,
  onStatus: (status: RuntimeStatus) => void,
) {
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const cursor = useRef(0);
  const serverId = useRef<string | null>(null);
  const serial = useRef(0);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    const controller = new AbortController();

    const poll = async () => {
      const currentSerial = ++serial.current;
      try {
        const status = await api.get<RuntimeStatus>("/rfid/runtime", controller.signal);
        if (disposed || currentSerial !== serial.current) return;
        setRuntime(status);
        onStatus(status);
        const reset = serverId.current !== null && serverId.current !== status.server_instance_id;
        if (serverId.current === null || reset) {
          serverId.current = status.server_instance_id;
          cursor.current = 0;
        }
        const batch = await api.get<EventBatch>(
          `/rfid/events?server_instance_id=${encodeURIComponent(status.server_instance_id)}&after_sequence=${cursor.current}&limit=100`,
          controller.signal,
        );
        if (disposed || currentSerial !== serial.current) return;
        if (batch.reset_required) cursor.current = 0;
        if (!batch.reset_required) {
          cursor.current = batch.next_after_sequence;
          onEvents(batch.events);
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          // 接続エラーは次回ポーリングで再試行する。画面を壊さない。
        }
      } finally {
        if (!disposed) timer = window.setTimeout(poll, 1000);
      }
    };

    if (enabled) void poll();
    return () => {
      disposed = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [enabled, onEvents, onStatus]);

  return runtime;
}
