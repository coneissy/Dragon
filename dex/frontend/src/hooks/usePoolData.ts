import { useEffect, useRef, useState, useCallback } from "react";
import type { PoolGroup, WsMessage } from "../types";

const WS_URL = "ws://localhost:3001";
const RECONNECT_DELAY = 2000;

export function usePoolData() {
  const [groups, setGroups] = useState<PoolGroup[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);

    ws.onclose = () => {
      setConnected(false);
      reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (event) => {
      const msg: WsMessage = JSON.parse(event.data);

      if (msg.type === "snapshot") {
        setGroups(msg.groups);
        setLastUpdate(Date.now());
      } else if (msg.type === "pool_update") {
        setGroups((prev) =>
          prev.map((g) =>
            g.name === msg.group
              ? {
                  ...g,
                  pools: g.pools.map((p) =>
                    p.address === msg.pool.address ? msg.pool : p,
                  ),
                }
              : g,
          ),
        );
        setLastUpdate(Date.now());
      }
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const poolCount = groups.reduce((sum, g) => sum + g.pools.length, 0);

  return { groups, connected, lastUpdate, poolCount };
}
