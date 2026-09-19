import {account, config} from "@/config";
import {POOL_GROUPS} from "@/config/pools";
import { createPool } from "@/pools/pool-factory";
import { PoolStateStore } from "@/core/pool-state";
import { WsServer } from "@/server/ws-server";
import { logger } from "@/utils/logger";
import {createPublicClient, http, PublicClient, webSocket} from "viem";
import {base} from "viem/chains";

export class Bot {
  private httpClient: PublicClient;
  private wsClient: PublicClient;
  private stores: PoolStateStore[] = [];
  private wsServer?: WsServer;

  constructor() {
    this.httpClient = createPublicClient({
      chain: base,
      transport: http(config.rpcHttp),
      batch: { multicall: true },
    }) as PublicClient;
    this.wsClient = createPublicClient({
      chain: base,
      transport: webSocket(config.rpcWs, {
        keepAlive: { interval: 10_000 },
        reconnect: { attempts: 20, delay: 3_000 },
        retryCount: 5,
        retryDelay: 1_000,
      }),
    }) as PublicClient;
  }

  async start(): Promise<void> {
    logger.info("Starting arb bot...");

    // Create a store per pool group
    for (const group of POOL_GROUPS) {
      const store = new PoolStateStore(group.name, this.httpClient, this.wsClient);
      for (const poolCfg of group.pools) {
        store.register(createPool({
          address: poolCfg.address,
          token0: group.token0,
          token1: group.token1,
          poolType: poolCfg.poolType,
          fee: poolCfg.fee,
          label: poolCfg.label,
        }));
      }
      this.stores.push(store);
    }

    await Promise.all(this.stores.map(s => s.loadStates()));

    logger.info({ address: account.address, dryRun: config.dryRun }, "Wallet loaded");

    this.wsServer = new WsServer(this.stores);
    await Promise.all(
      this.stores.map(store =>
        store.startListening((pool) => this.wsServer!.broadcastUpdates(store, pool))
      )
    );

    logger.info(
      {
        pools: this.stores.reduce((sum, s) => sum + s.getAll().length, 0),
        groups: POOL_GROUPS.length,
        dryRun: config.dryRun,
      },
      "Bot started",
    );

  }

  stop(): void {
    this.wsServer?.close();
    logger.info("Bot stopped");
  }
}
