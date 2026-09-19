import { WebSocketServer, type WebSocket } from "ws";
import type { Address } from "viem";
import type { PoolStateStore } from "@/core/pool-state";
import {tokenMap} from "@/config/tokens";
import { logger } from "@/utils/logger";
import {IPoolMath, PoolType, type V2PoolState, type V3PoolState} from "@/pools/types";
import {v3PriceFromSqrtX96} from "@/pools/types/uniswap-v3";

const DEFAULT_PORT = 3001;

export class WsServer {
  private wss: WebSocketServer;
  private stores: PoolStateStore[];

  constructor(stores: PoolStateStore[]) {
    this.stores = stores;

    const port = parseInt(process.env.WS_PORT ?? String(DEFAULT_PORT), 10);
    this.wss = new WebSocketServer({ port });

    this.wss.on("connection", (ws) => {
      logger.debug("Dashboard client connected");
      this.sendSnapshot(ws);

      ws.on("close", () => {
        logger.debug("Dashboard client disconnected");
      });
    });

    logger.info({ port }, "WS dashboard server started");
  }

  private sendSnapshot(ws: WebSocket): void {
    const groups = this.stores.map(store => ({
      name: store.name,
      pools: store.getAll().map((p) => this.serializePool(p)),
    }));

    const msg = JSON.stringify({ type: "snapshot", groups });
    ws.send(msg);
  }

  broadcastUpdates(store: PoolStateStore, pool: IPoolMath): void {
    if (this.wss.clients.size === 0) return;

    const serialized = this.serializePool(pool);
    const msg = JSON.stringify({ type: "pool_update", group: store.name, pool: serialized });

    for (const client of this.wss.clients) {
      if (client.readyState === 1) {
        client.send(msg);
      }
    }
  }

  private serializePool(
    pool: IPoolMath,
  ): SerializedPool {
    const { config, state } = pool;
    const t0 = tokenMap.get(config.token0.toLowerCase() as Address);
    const t1 = tokenMap.get(config.token1.toLowerCase() as Address);

    const result: SerializedPool = {
      address: config.address,
      token0: {
        symbol: t0?.symbol ?? "???",
        address: config.token0,
        decimals: t0?.decimals ?? 18,
      },
      token1: {
        symbol: t1?.symbol ?? "???",
        address: config.token1,
        decimals: t1?.decimals ?? 18,
      },
      poolType: config.poolType,
      fee: config.fee,
      label: config.label,
      lastUpdate: state.lastUpdate ?? Date.now(),
    };

    const isV3 = config.poolType === PoolType.UniswapV3 || config.poolType === PoolType.PancakeV3;

    if (isV3) {
      const s = state as V3PoolState;
      if (s.sqrtPriceX96 != null) {
        result.sqrtPriceX96 = s.sqrtPriceX96.toString();
        result.tick = s.tick;
        result.liquidity = s.liquidity?.toString();

        const decimals0 = t0?.decimals ?? 18;
        const decimals1 = t1?.decimals ?? 18;
        const price0 = v3PriceFromSqrtX96(s.sqrtPriceX96, decimals0, decimals1);
        if (price0 > 0) {
          result.price0 = price0;
          result.price1 = 1 / price0;
        }

        if (s.liquidity) {
          const Q96 = 2n ** 96n;
          const amount1Raw = s.liquidity * s.sqrtPriceX96 / Q96;
          const amount1Human = Number(amount1Raw) / 10 ** decimals1;
          result.tvlQuote = amount1Human * 2;
        }
      }
    } else {
      const s = state as V2PoolState;
      if (s.reserve0 != null && s.reserve1 != null) {
        result.reserve0 = s.reserve0.toString();
        result.reserve1 = s.reserve1.toString();

        const decimals0 = t0?.decimals ?? 18;
        const decimals1 = t1?.decimals ?? 18;

        const r0 = Number(s.reserve0) / 10 ** decimals0;
        const r1 = Number(s.reserve1) / 10 ** decimals1;

        if (r0 > 0 && r1 > 0) {
          result.price0 = r1 / r0;
          result.price1 = r0 / r1;
        }

        result.tvlQuote = r1 * 2;
      }
    }

    return result;
  }

  close(): void {
    this.wss.close();
  }
}

export interface SerializedPool {
  address: string;
  token0: { symbol: string; address: string; decimals: number };
  token1: { symbol: string; address: string; decimals: number };
  poolType: string;
  fee: number;
  label?: string;
  reserve0?: string;
  reserve1?: string;
  sqrtPriceX96?: string;
  tick?: number;
  liquidity?: string;
  price0?: number;
  price1?: number;
  tvlQuote?: number;
  lastUpdate: number;
}
