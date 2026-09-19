import {Address, PublicClient} from "viem";
import {InitialStateRequest, IPoolMath, PoolType} from "@/pools/types";
import {logger} from "@/utils/logger";
import {SYNC_EVENT, V2Pool} from "@/pools/types/uniswap-v2";
import {V3_SWAP_EVENT, V3Pool} from "@/pools/types/uniswap-v3";
import {PANCAKE_V3_SWAP_EVENT, PancakeV3Pool} from "@/pools/types/pancakeswap-v3";
import {AERODROME_SYNC_EVENT, AerodromeV2Pool} from "@/pools/types/aerodrome-v2";
import {findOpportunitySimple, getEthPriceUsd} from "@/core/algos/find-opportunity-simple";
import {execute} from "@/core/execute";

const TICK_RESYNC_INTERVAL_MS = 60_000;

export class PoolStateStore {
  readonly name: string;
  private pools = new Map<Address, IPoolMath>();
  httpClient: PublicClient;
  wsClient: PublicClient;
  private arbScheduled = false;
  private arbRunning = false;
  private tickResyncTimer: ReturnType<typeof setInterval> | null = null;

  constructor(name: string, httpClient: PublicClient, wsClient: PublicClient) {
    this.name = name;
    this.httpClient = httpClient;
    this.wsClient = wsClient;
  }

  register(pool: IPoolMath): void {
    this.pools.set(pool.config.address.toLowerCase() as Address, pool);
  }

  get(address: Address): IPoolMath | undefined {
    return this.pools.get(address.toLowerCase() as Address);
  }

  getAll(): IPoolMath[] {
    return Array.from(this.pools.values());
  }

  async loadStates(): Promise<void> {
    const allPools = this.getAll();

    // Phase 1: slot0 + liquidity + fee + tickSpacing
    const entries: { pool: IPoolMath; startIdx: number; count: number }[] = [];
    const allCalls: InitialStateRequest[] = [];
    for (const pool of allPools) {
      const reqs = pool.getInitialRequests();
      entries.push({ pool, startIdx: allCalls.length, count: reqs.length });
      allCalls.push(...reqs);
    }

    const results = await this.httpClient.multicall({ contracts: allCalls });

    let loaded = 0;
    for (const { pool, startIdx, count } of entries) {
      const slice = results.slice(startIdx, startIdx + count);
      if (pool.applyInitialResults(slice)) loaded++;
    }

    logger.info(
      { total: allPools.length, loaded },
      "Phase 1: Pool states loaded via multicall",
    );

    // Phase 2 & 3: Load tick data for V3 pools
    await this.reloadTickData();

    // Start periodic tick re-sync
    if (this.tickResyncTimer) clearInterval(this.tickResyncTimer);
    this.tickResyncTimer = setInterval(() => {
      this.reloadTickData().catch((err) => {
        logger.warn({ err }, "Tick data re-sync failed");
      });
    }, TICK_RESYNC_INTERVAL_MS);
  }

  private getV3Pools(): (V3Pool | PancakeV3Pool)[] {
    return this.getAll().filter(
      (p) => p.config.poolType === PoolType.UniswapV3 || p.config.poolType === PoolType.PancakeV3,
    ) as (V3Pool | PancakeV3Pool)[];
  }

  async reloadTickData(): Promise<void> {
    const v3Pools = this.getV3Pools();
    if (v3Pools.length === 0) return;

    // Phase 2: batch all bitmap calls into 1 multicall
    const bitmapEntries: { pool: V3Pool | PancakeV3Pool; startIdx: number; count: number }[] = [];
    const bitmapCalls: InitialStateRequest[] = [];
    for (const pool of v3Pools) {
      const calls = pool.getBitmapCalls();
      if (!calls) continue;
      bitmapEntries.push({ pool, startIdx: bitmapCalls.length, count: calls.length });
      bitmapCalls.push(...calls);
    }

    if (bitmapCalls.length === 0) return;
    const bitmapResults = await this.httpClient.multicall({ contracts: bitmapCalls });

    // Phase 3: batch all tick data calls into 1 multicall
    const tickEntries: { pool: V3Pool | PancakeV3Pool; ticks: number[]; startIdx: number }[] = [];
    const tickCalls: InitialStateRequest[] = [];

    for (const { pool, startIdx, count } of bitmapEntries) {
      const initializedTicks = pool.applyBitmapResults(bitmapResults.slice(startIdx, startIdx + count));
      if (initializedTicks.length > 0) {
        const calls = pool.getTickDataCalls(initializedTicks);
        tickEntries.push({ pool, ticks: initializedTicks, startIdx: tickCalls.length });
        tickCalls.push(...calls);
      }
    }

    if (tickCalls.length === 0) return;
    const tickResults = await this.httpClient.multicall({ contracts: tickCalls });

    let totalTicks = 0;
    for (const { pool, ticks, startIdx } of tickEntries) {
      totalTicks += pool.applyTickDataResults(ticks, tickResults.slice(startIdx, startIdx + ticks.length));
    }

    logger.info({ v3Pools: v3Pools.length, totalTicks }, "Tick data loaded");
  }

  async startListening(
    broadcastCallback: (pool: IPoolMath) => void,
  ): Promise<void> {
    const v2Pools = this.getAll().filter((p) => p.config.poolType === PoolType.UniswapV2);
    const aeroV2Pools = this.getAll().filter((p) => p.config.poolType === PoolType.AerodromeV2);
    const v3Pools = this.getAll().filter((p) => p.config.poolType === PoolType.UniswapV3);
    const pancakeV3Pools = this.getAll().filter((p) => p.config.poolType === PoolType.PancakeV3);

    const v2ByAddress = new Map(v2Pools.map(p => [p.config.address.toLowerCase(), p as V2Pool]));
    const aeroV2ByAddress = new Map(aeroV2Pools.map(p => [p.config.address.toLowerCase(), p as AerodromeV2Pool]));
    const v3ByAddress = new Map(v3Pools.map(p => [p.config.address.toLowerCase(), p as V3Pool]));
    const pancakeV3ByAddress = new Map(pancakeV3Pools.map(p => [p.config.address.toLowerCase(), p as PancakeV3Pool]));

    const onPoolEvent = (pool: IPoolMath) => {
      broadcastCallback(pool);
      this.scheduleArbitrage();
    };

    const onWsError = (err: Error) => {
      logger.warn({ err: err.message, group: this.name }, "WS watchEvent error");
    };

    if (v2Pools.length > 0) {
      this.wsClient.watchEvent({
        address: v2Pools.map(p => p.config.address),
        event: SYNC_EVENT,
        onError: onWsError,
        onLogs: logs => {
          logs.forEach(log => {
            const pool = v2ByAddress.get(log.address.toLowerCase());
            if (pool) { pool.handleSyncEvent(log); onPoolEvent(pool); }
          });
        },
      });
    }

    if (aeroV2Pools.length > 0) {
      this.wsClient.watchEvent({
        address: aeroV2Pools.map(p => p.config.address),
        event: AERODROME_SYNC_EVENT,
        onError: onWsError,
        onLogs: logs => {
          logs.forEach(log => {
            const pool = aeroV2ByAddress.get(log.address.toLowerCase());
            if (pool) { pool.handleSyncEvent(log); onPoolEvent(pool); }
          });
        },
      });
    }

    if (v3Pools.length > 0) {
      this.wsClient.watchEvent({
        address: v3Pools.map(p => p.config.address),
        event: V3_SWAP_EVENT,
        onError: onWsError,
        onLogs: logs => {
          logs.forEach(log => {
            const pool = v3ByAddress.get(log.address.toLowerCase());
            if (pool) { pool.handleSwapEvent(log); onPoolEvent(pool); }
          });
        },
      });
    }

    if (pancakeV3Pools.length > 0) {
      this.wsClient.watchEvent({
        address: pancakeV3Pools.map(p => p.config.address),
        event: PANCAKE_V3_SWAP_EVENT,
        onError: onWsError,
        onLogs: logs => {
          logs.forEach(log => {
            const pool = pancakeV3ByAddress.get(log.address.toLowerCase());
            if (pool) { pool.handleSwapEvent(log); onPoolEvent(pool); }
          });
        },
      });
    }

    // Mint/Burn events are handled by periodic tick resync (every 60s)
    // to stay within Alchemy's WebSocket subscription limit
  }

  scheduleArbitrage() {
    if (this.arbScheduled) return;
    this.arbScheduled = true;
    queueMicrotask(() => {
      this.arbScheduled = false;
      this.arbitrage();
    });
  }

  private arbitrage() {
    if (this.arbRunning) return;
    const pools = this.getAll()
    const opp = findOpportunitySimple(pools)
    if (!opp) return;
    this.arbRunning = true;
    execute(opp, getEthPriceUsd(pools), this.httpClient).finally(() => {
      this.arbRunning = false;
    });
  }
}
