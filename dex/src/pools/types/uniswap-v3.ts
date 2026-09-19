import {Address, decodeEventLog, Log, parseAbiItem} from "viem";
import type {
  IPoolMath,
  InitialStateRequest,
  InitialStateResult,
  PoolConfig,
  V3PoolState,
  V3TickData,
} from "@/pools/types";
import { logger } from "@/utils/logger";
import { v3GetAmountOut, v3GetAmountOutCrossTicks } from "@/pools/v3-math";

const V3_POOL_ABI = [
  {
    inputs: [],
    name: "slot0",
    outputs: [
      { name: "sqrtPriceX96", type: "uint160" },
      { name: "tick", type: "int24" },
    ],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [],
    name: "liquidity",
    outputs: [{ name: "", type: "uint128" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [],
    name: "fee",
    outputs: [{ name: "", type: "uint24" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [],
    name: "tickSpacing",
    outputs: [{ name: "", type: "int24" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [{ name: "wordPosition", type: "int16" }],
    name: "tickBitmap",
    outputs: [{ name: "", type: "uint256" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [{ name: "tick", type: "int24" }],
    name: "ticks",
    outputs: [
      { name: "liquidityGross", type: "uint128" },
      { name: "liquidityNet", type: "int128" },
      { name: "feeGrowthOutside0X128", type: "uint256" },
      { name: "feeGrowthOutside1X128", type: "uint256" },
      { name: "tickCumulativeOutside", type: "int56" },
      { name: "secondsPerLiquidityOutsideX128", type: "uint160" },
      { name: "secondsOutside", type: "uint32" },
      { name: "initialized", type: "bool" },
    ],
    stateMutability: "view",
    type: "function",
  },
] as const;

export class V3Pool implements IPoolMath {
  readonly config: PoolConfig;
  state: V3PoolState;
  tickData: V3TickData | null = null;
  private feePips = 0;

  constructor(config: PoolConfig, state: V3PoolState = {}) {
    this.config = config;
    this.state = state;
    this.feePips = Math.round(config.fee * 1_000_000);
  }

  getAmountOut(amountIn: bigint, zeroForOne: boolean): bigint {
    const { sqrtPriceX96, liquidity, tick } = this.state;
    if (!sqrtPriceX96 || !liquidity) return 0n;

    if (this.tickData && this.tickData.sortedTicks.length > 0 && tick !== undefined) {
      return v3GetAmountOutCrossTicks(
        sqrtPriceX96, liquidity, tick, amountIn, zeroForOne, this.feePips, this.tickData,
      );
    }
    return v3GetAmountOut(sqrtPriceX96, liquidity, amountIn, zeroForOne, this.config.fee);
  }

  getInitialRequests(): InitialStateRequest[] {
    return [
      { address: this.config.address, abi: V3_POOL_ABI, functionName: "slot0" },
      { address: this.config.address, abi: V3_POOL_ABI, functionName: "liquidity" },
      { address: this.config.address, abi: V3_POOL_ABI, functionName: "fee" },
      { address: this.config.address, abi: V3_POOL_ABI, functionName: "tickSpacing" },
    ];
  }

  applyInitialResults(results: InitialStateResult[]): boolean {
    const slot0Res = results[0];
    const liqRes = results[1];
    const feeRes = results[2];
    const tickSpacingRes = results[3];
    if (slot0Res.status !== "success" || liqRes.status !== "success") {
      logger.warn({ pool: this.config.address, slot0Err: (slot0Res as any).error?.message, liqErr: (liqRes as any).error?.message }, "Failed to load V3 state");
      return false;
    }
    const slot0 = slot0Res.result as readonly [bigint, number];
    const liq = liqRes.result as bigint;
    this.state.sqrtPriceX96 = BigInt(slot0[0]);
    this.state.tick = Number(slot0[1]);
    this.state.liquidity = BigInt(liq);
    this.state.lastUpdate = Date.now();

    if (feeRes.status === "success") {
      this.config.fee = Number(feeRes.result as bigint) / 1_000_000;
      this.feePips = Math.round(this.config.fee * 1_000_000);
    }
    if (tickSpacingRes?.status === "success") {
      this.state.tickSpacing = Number(tickSpacingRes.result);
    }
    return true;
  }

  // ── Tick data loading ──

  getBitmapCalls(): InitialStateRequest[] | null {
    const { tick, tickSpacing } = this.state;
    if (tick === undefined || !tickSpacing) return null;

    const currentWord = Math.floor(tick / tickSpacing) >> 8;
    const calls: InitialStateRequest[] = [];
    for (let i = currentWord - 16; i <= currentWord + 15; i++) {
      calls.push({
        address: this.config.address,
        abi: V3_POOL_ABI,
        functionName: "tickBitmap",
        args: [i],
      } as any);
    }
    return calls;
  }

  applyBitmapResults(results: InitialStateResult[]): number[] {
    const { tick, tickSpacing } = this.state;
    const currentWord = Math.floor(tick! / tickSpacing!) >> 8;
    const initializedTicks: number[] = [];
    for (let wi = 0; wi < results.length; wi++) {
      const wordPos = currentWord - 16 + wi;
      const res = results[wi];
      if (res.status !== "success") continue;
      const bitmap = BigInt(res.result as bigint);
      if (bitmap === 0n) continue;

      for (let bit = 0; bit < 256; bit++) {
        if ((bitmap >> BigInt(bit)) & 1n) {
          initializedTicks.push(((wordPos * 256) + bit) * tickSpacing!);
        }
      }
    }
    return initializedTicks;
  }

  getTickDataCalls(tickIndices: number[]): InitialStateRequest[] {
    return tickIndices.map((idx) => ({
      address: this.config.address,
      abi: V3_POOL_ABI,
      functionName: "ticks",
      args: [idx],
    }));
  }

  applyTickDataResults(tickIndices: number[], results: InitialStateResult[]): number {
    const tickMap = new Map<number, bigint>();
    for (let i = 0; i < tickIndices.length; i++) {
      const res = results[i];
      if (res.status !== "success") continue;
      const data = res.result as readonly [bigint, bigint, ...unknown[]];
      const liquidityNet = data[1];
      if (liquidityNet !== 0n) {
        tickMap.set(tickIndices[i], liquidityNet);
      }
    }
    this.tickData = {
      sortedTicks: Array.from(tickMap.keys()).sort((a, b) => a - b),
      tickMap,
    };
    return tickMap.size;
  }

  handleSwapEvent(log: Log) {
    const decoded = decodeEventLog({
      abi: V3_SWAP_ABI,
      data: log.data,
      topics: log.topics,
    });

    this.state.sqrtPriceX96 = decoded.args.sqrtPriceX96;
    this.state.tick = decoded.args.tick;
    this.state.liquidity = decoded.args.liquidity;
    this.state.lastUpdate = Date.now();
  }

  handleMintEvent(log: Log): void {
    const decoded = decodeEventLog({ abi: V3_MINT_ABI, data: log.data, topics: log.topics });
    const { tickLower, tickUpper, amount } = decoded.args;
    this.updateTickLiquidity(Number(tickLower), Number(tickUpper), BigInt(amount));
  }

  handleBurnEvent(log: Log): void {
    const decoded = decodeEventLog({ abi: V3_BURN_ABI, data: log.data, topics: log.topics });
    const { tickLower, tickUpper, amount } = decoded.args;
    this.updateTickLiquidity(Number(tickLower), Number(tickUpper), -BigInt(amount));
  }

  private updateTickLiquidity(tickLower: number, tickUpper: number, liquidityDelta: bigint): void {
    if (!this.tickData) return;

    const { tickMap } = this.tickData;
    const sizeBefore = tickMap.size;

    const newLower = (tickMap.get(tickLower) ?? 0n) + liquidityDelta;
    if (newLower !== 0n) tickMap.set(tickLower, newLower); else tickMap.delete(tickLower);

    const newUpper = (tickMap.get(tickUpper) ?? 0n) - liquidityDelta;
    if (newUpper !== 0n) tickMap.set(tickUpper, newUpper); else tickMap.delete(tickUpper);

    // Only rebuild sorted array when keys actually changed
    if (tickMap.size !== sizeBefore) {
      this.tickData.sortedTicks = Array.from(tickMap.keys()).sort((a, b) => a - b);
    }

    const currentTick = this.state.tick;
    if (currentTick !== undefined && currentTick >= tickLower && currentTick < tickUpper) {
      this.state.liquidity = (this.state.liquidity ?? 0n) + liquidityDelta;
    }

    this.state.lastUpdate = Date.now();
  }
}

/**
 * Simple V3 price estimation from sqrtPriceX96 (for quick filtering, not execution).
 * price = (sqrtPriceX96 / 2^96)^2
 */
export function v3PriceFromSqrtX96(
  sqrtPriceX96: bigint,
  decimals0: number,
  decimals1: number,
): number {
  const price =
    Number(sqrtPriceX96 * sqrtPriceX96) / Number(1n << 192n);
  const decimalAdj = 10 ** (decimals0 - decimals1);
  return price * decimalAdj;
}

export const V3_SWAP_EVENT = parseAbiItem(
  "event Swap(address indexed sender, address indexed recipient, int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick)",
);

export const V3_MINT_EVENT = parseAbiItem(
  "event Mint(address sender, address indexed owner, int24 indexed tickLower, int24 indexed tickUpper, uint128 amount, uint256 amount0, uint256 amount1)",
);

export const V3_BURN_EVENT = parseAbiItem(
  "event Burn(address indexed owner, int24 indexed tickLower, int24 indexed tickUpper, uint128 amount, uint256 amount0, uint256 amount1)",
);

const V3_MINT_ABI = [
  {
    type: "event",
    name: "Mint",
    inputs: [
      { name: "sender", type: "address", indexed: false },
      { name: "owner", type: "address", indexed: true },
      { name: "tickLower", type: "int24", indexed: true },
      { name: "tickUpper", type: "int24", indexed: true },
      { name: "amount", type: "uint128", indexed: false },
      { name: "amount0", type: "uint256", indexed: false },
      { name: "amount1", type: "uint256", indexed: false },
    ],
  },
] as const;

const V3_BURN_ABI = [
  {
    type: "event",
    name: "Burn",
    inputs: [
      { name: "owner", type: "address", indexed: true },
      { name: "tickLower", type: "int24", indexed: true },
      { name: "tickUpper", type: "int24", indexed: true },
      { name: "amount", type: "uint128", indexed: false },
      { name: "amount0", type: "uint256", indexed: false },
      { name: "amount1", type: "uint256", indexed: false },
    ],
  },
] as const;

export const V3_SWAP_ABI = [
  {
    type: "event",
    name: "Swap",
    inputs: [
      { name: "sender", type: "address", indexed: true },
      { name: "recipient", type: "address", indexed: true },
      { name: "amount0", type: "int256", indexed: false },
      { name: "amount1", type: "int256", indexed: false },
      { name: "sqrtPriceX96", type: "uint160", indexed: false },
      { name: "liquidity", type: "uint128", indexed: false },
      { name: "tick", type: "int24", indexed: false },
    ],
  },
] as const;
