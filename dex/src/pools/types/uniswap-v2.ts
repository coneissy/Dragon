import type {
  IPoolMath,
  InitialStateRequest,
  InitialStateResult,
  PoolConfig,
  V2PoolState,
} from "@/pools/types";
import { logger } from "@/utils/logger";
import {decodeEventLog, type Log, parseAbiItem} from "viem";
import {v2GetAmountOut} from "@/pools/v2-math";

const V2_PAIR_ABI = [
  {
    inputs: [],
    name: "getReserves",
    outputs: [
      { name: "reserve0", type: "uint112" },
      { name: "reserve1", type: "uint112" },
      { name: "blockTimestampLast", type: "uint32" },
    ],
    stateMutability: "view",
    type: "function",
  },
] as const;

/**
 * Constant-product AMM math: x * y = k
 * Used for Uniswap V2, SushiSwap V2, BaseSwap, Aerodrome volatile pools.
 * Fee is taken from input before the swap.
 */
export class V2Pool implements IPoolMath {
  readonly config: PoolConfig;
  state: V2PoolState;
  constructor(config: PoolConfig, state: V2PoolState = {}) {
    this.config = config;
    this.state = state;
  }

  getAmountOut(amountIn: bigint, zeroForOne: boolean): bigint {
    const { reserve0, reserve1 } = this.state;
    if (!reserve0 || !reserve1 || reserve0 === 0n || reserve1 === 0n) return 0n;
    if (amountIn <= 0n) return 0n;

    const [reserveIn, reserveOut] = zeroForOne
      ? [reserve0, reserve1]
      : [reserve1, reserve0];

    return v2GetAmountOut(amountIn, reserveIn, reserveOut, this.config.fee);
  }

  getInitialRequests(): InitialStateRequest[] {
    return [{ address: this.config.address, abi: V2_PAIR_ABI, functionName: "getReserves" }];
  }

  applyInitialResults(results: InitialStateResult[]): boolean {
    const res = results[0];
    if (res.status !== "success") {
      logger.warn({ pool: this.config.address }, "Failed to load V2 reserves");
      return false;
    }
    const [reserve0, reserve1] = res.result as readonly [bigint, bigint, number];
    this.state.reserve0 = BigInt(reserve0);
    this.state.reserve1 = BigInt(reserve1);
    this.state.lastUpdate = Date.now();
    return true;
  }

  handleSyncEvent(log: Log): void {
    const decoded = decodeEventLog({
      abi: SYNC_ABI,
      data: log.data,
      topics: log.topics,
    });

    this.state.reserve0 = decoded.args.reserve0;
    this.state.reserve1 = decoded.args.reserve1;
    this.state.lastUpdate = Date.now();
  }
}

export const SYNC_EVENT = parseAbiItem(
  "event Sync(uint112 reserve0, uint112 reserve1)",
);

export const SYNC_ABI = [
  {
    type: "event",
    name: "Sync",
    inputs: [
      { name: "reserve0", type: "uint112", indexed: false },
      { name: "reserve1", type: "uint112", indexed: false },
    ],
  },
] as const;
