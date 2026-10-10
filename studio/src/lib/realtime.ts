"use client";

import { useEffect, useRef, useState } from "react";

import { api, type PushSignal } from "@/lib/api";

const RETRY_MS = 5000;

/**
 * Live push from Azure Web PubSub. Asks the API for a short-lived URL that joins the match's
 * group, opens a WebSocket and calls `onSignal` for each message; reconnects with a fresh URL
 * after a drop. Returns whether push is connected (callers poll faster when it is not).
 * When push is off on the server the URL is null and this stays disconnected.
 */
export function useRealtime(
  matchId: string | null,
  producer: boolean,
  onSignal: (signal: PushSignal) => void,
): boolean {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onSignal);

  useEffect(() => {
    handler.current = onSignal;
  }, [onSignal]);

  useEffect(() => {
    if (!matchId) return;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    const connect = async () => {
      try {
        const { url } = await api.negotiate(matchId, producer);
        if (!url || stopped) return; // push is off: stay on polling
        socket = new WebSocket(url);
        socket.onopen = () => setConnected(true);
        socket.onmessage = (event) => {
          try {
            handler.current(JSON.parse(String(event.data)) as PushSignal);
          } catch {
            // ignore anything that is not a signal
          }
        };
        socket.onclose = () => {
          setConnected(false);
          if (!stopped) retry = setTimeout(connect, RETRY_MS);
        };
      } catch {
        if (!stopped) retry = setTimeout(connect, RETRY_MS);
      }
    };
    connect();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      socket?.close();
      setConnected(false);
    };
  }, [matchId, producer]);

  return connected;
}
