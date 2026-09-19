import type { Address } from "viem";
import { logger } from "@/utils/logger";
import {IPoolMath, PoolType, V2PoolState, V3PoolState, V3TickData, type PoolConfig} from "@/pools/types";
import {getMaxFlash} from "@/core/loan";
import {isDebugEnabled, startDebugEntry, type DebugEntry, type DebugPoolSnapshot, type DebugSwapStep} from "@/utils/debug-log";
import {v3GetAmountOutCrossTicks} from "@/pools/v3-math";

/**
 * For each pool pair, checks two directions:
 *   direction 0: borrow token0 → pool0(token0→token1) → pool1(token1→token0) → repay
 *   direction 1: borrow token1 → pool0(token1→token0) → pool1(token0→token1) → repay
 */
export function findOpportunitySimple(
  pools: IPoolMath[],
): ArbOpportunity | undefined {
  const startMs = performance.now();
  const debug = isDebugEnabled();

  const opportunities: ArbOpportunity[] = [];

  for (let i = 0; i < pools.length; i++) {
    for (let j = i + 1; j < pools.length; j++) {
      const poolA = pools[i];
      const poolB = pools[j];

      // Two directions per pair, both borrow token0:
      //   A→B: poolA(token0→token1) → poolB(token1→token0) — profit when poolA has cheaper token1
      //   B→A: poolB(token0→token1) → poolA(token1→token0) — profit when poolB has cheaper token1
      const loanToken = poolA.config.token0;
      const directions: { pool0: IPoolMath; pool1: IPoolMath }[] = [
        { pool0: poolA, pool1: poolB },
        { pool0: poolB, pool1: poolA },
      ];

      const maxFlash = getMaxFlash(loanToken);

      for (const dir of directions) {
        const debugSearchSteps: SearchStep[] | undefined = debug ? [] : undefined;
        const result = findOptimalInput(
          dir.pool0,
          dir.pool1,
          true,  // zeroForOne0: token0 → token1
          false, // zeroForOne1: token1 → token0
          maxFlash,
          debugSearchSteps,
        );
        if (!result) continue;

        const [optimalIn, netProfit, bridgeAmount, amountOut] = result;
        const flashloanFee = (optimalIn * FLASHLOAN_FEE_BPS) / 10000n;

        let debugEntry: DebugEntry | undefined;
        if (debug) {
          debugEntry = startDebugEntry()!;
          debugEntry.pool0Snapshot = snapshotPool(dir.pool0);
          debugEntry.pool1Snapshot = snapshotPool(dir.pool1);
          debugEntry.loanToken = loanToken;
          debugEntry.maxFlash = maxFlash.toString();
          debugEntry.searchSteps = debugSearchSteps!;
          debugEntry.optimalIn = optimalIn.toString();
          debugEntry.swap0Result = bridgeAmount.toString();
          debugEntry.swap1Result = amountOut.toString();
          debugEntry.flashloanFee = flashloanFee.toString();
          debugEntry.netProfit = netProfit.toString();

          // Re-run swaps with debug steps to capture V3 tick-crossing detail
          const swap0Steps: DebugSwapStep[] = [];
          const swap1Steps: DebugSwapStep[] = [];
          rerunSwapWithDebug(dir.pool0, optimalIn, true, swap0Steps);
          rerunSwapWithDebug(dir.pool1, bridgeAmount, false, swap1Steps);
          if (swap0Steps.length > 0) debugEntry.swap0Steps = swap0Steps;
          if (swap1Steps.length > 0) debugEntry.swap1Steps = swap1Steps;
        }

        opportunities.push({
          loanToken,
          pool0: dir.pool0.config,
          pool1: dir.pool1.config,
          zeroForOne0: true,
          zeroForOne1: false,
          optimalIn,
          netProfit,
          expectedBridgeAmount: bridgeAmount,
          expectedAmountOut: amountOut,
          debugEntry,
        });
      }
    }
  }

  // Sort by net profit descending
  opportunities.sort((a, b) =>
    a.netProfit > b.netProfit ? -1 : a.netProfit < b.netProfit ? 1 : 0,
  );

  if (opportunities.length > 0) {
    logger.debug(
      { count: opportunities.length, bestProfit: opportunities[0].netProfit.toString() },
      "Arb opportunities found",
    );
  }

  const mathMs = performance.now() - startMs;

  if (opportunities.length === 0) {
    logger.debug(
      { mathMs: mathMs.toFixed(1) },
      "No arb opportunities",
    );
    return;
  }

  // Execute best opportunity
  const best = opportunities[0]; // Already sorted by profit
  const ethPrice = getEthPriceUsd(pools);
  logger.info(
    {
      profit: best.netProfit.toString(),
      profitUsd: formatUsd(best.netProfit, ethPrice),
      optimalIn: formatEth(best.optimalIn),
      loanToken: best.loanToken,
      pool0: best.pool0.address,
      pool1: best.pool1.address,
      mathMs: mathMs.toFixed(1),
    },
    "Arb opportunity found",
  );

  return best;
}

export interface ArbOpportunity {
  loanToken: Address;
  pool0: PoolConfig;
  pool1: PoolConfig;
  zeroForOne0: boolean;
  zeroForOne1: boolean;
  optimalIn: bigint;
  netProfit: bigint; // after flashloan fee, before gas
  expectedBridgeAmount: bigint; // expected output of swap0
  expectedAmountOut: bigint;    // expected output of swap1
  debugEntry?: DebugEntry;
}

const FLASHLOAN_FEE_BPS = 5n; // Aave V3: 5 bps

function calcProfit(
  amountIn: bigint,
  pool0: IPoolMath,
  pool1: IPoolMath,
  zeroForOne0: boolean,
  zeroForOne1: boolean,
): bigint {
  if (amountIn <= 0n) return -1n;

  const amountBridge = pool0.getAmountOut(amountIn, zeroForOne0);
  if (amountBridge <= 0n) return -1n;

  const amountOut = pool1.getAmountOut(amountBridge, zeroForOne1);
  if (amountOut <= 0n) return -1n;

  const flashloanFee = (amountIn * FLASHLOAN_FEE_BPS) / 10000n;
  return amountOut - amountIn - flashloanFee;
}

interface SearchStep { lo: string; hi: string; mid1: string; mid2: string; p1: string; p2: string }

function findOptimalInput(
  pool0: IPoolMath,
  pool1: IPoolMath,
  zeroForOne0: boolean,
  zeroForOne1: boolean,
  maxInput: bigint,
  debugSearchSteps?: SearchStep[],
): [optimalIn: bigint, netProfit: bigint, bridgeAmount: bigint, amountOut: bigint] | null {
  // Quick check: is there any profit at all?
  const testAmounts = [maxInput / 1000n, maxInput / 100n, maxInput / 10n];
  let anyPositive = false;
  for (const amt of testAmounts) {
    if (amt > 0n && calcProfit(amt, pool0, pool1, zeroForOne0, zeroForOne1) > 0n) {
      anyPositive = true;
      break;
    }
  }
  if (!anyPositive) return null;

  // Ternary search over [1, maxInput]
  let lo = 1n;
  let hi = maxInput;

  for (let i = 0; i < 128; i++) {
    if (hi - lo < 3n) break;

    const mid1 = lo + (hi - lo) / 3n;
    const mid2 = hi - (hi - lo) / 3n;

    const p1 = calcProfit(mid1, pool0, pool1, zeroForOne0, zeroForOne1);
    const p2 = calcProfit(mid2, pool0, pool1, zeroForOne0, zeroForOne1);

    if (debugSearchSteps) {
      debugSearchSteps.push({
        lo: lo.toString(), hi: hi.toString(),
        mid1: mid1.toString(), mid2: mid2.toString(),
        p1: p1.toString(), p2: p2.toString(),
      });
    }

    if (p1 < p2) {
      lo = mid1;
    } else {
      hi = mid2;
    }
  }

  const optimal = (lo + hi) / 2n;
  const bridgeAmount = pool0.getAmountOut(optimal, zeroForOne0);
  const amountOut = pool1.getAmountOut(bridgeAmount, zeroForOne1);
  const flashloanFee = (optimal * FLASHLOAN_FEE_BPS) / 10000n;
  const profit = amountOut - optimal - flashloanFee;

  if (profit <= 0n) return null;
  return [optimal, profit, bridgeAmount, amountOut];
}

/** Get ETH price in USD from the first V2 pool with reserves */
export function getEthPriceUsd(pools: IPoolMath[]): number {
  for (const pool of pools) {
    const pt = pool.config.poolType;
    if (pt !== PoolType.UniswapV2 && pt !== PoolType.AerodromeV2) continue;
    const s = pool.state as V2PoolState;
    if (!s.reserve0 || !s.reserve1) continue;
    // token0 = WETH (18 dec), token1 = USDC (6 dec)
    // price = (reserve1 / 10^6) / (reserve0 / 10^18) = reserve1 / reserve0 * 10^12
    return Number(s.reserve1) / Number(s.reserve0) * 1e12;
  }
  return 0;
}

export function formatUsd(weiAmount: bigint, ethPrice: number): string {
  if (ethPrice === 0) return "$?.??";
  const usd = Number(weiAmount) / 1e18 * ethPrice;
  return `$${usd.toFixed(2)}`;
}

function formatEth(wei: bigint): string {
  const eth = Number(wei) / 1e18;
  return `${eth.toPrecision(4)} WETH`;
}

function snapshotPool(pool: IPoolMath): DebugPoolSnapshot {
  const snap: DebugPoolSnapshot = {
    address: pool.config.address,
    poolType: pool.config.poolType,
    fee: pool.config.fee,
    label: pool.config.label,
  };
  const s = pool.state;
  if ("reserve0" in s) {
    snap.reserve0 = s.reserve0?.toString();
    snap.reserve1 = s.reserve1?.toString();
  }
  if ("sqrtPriceX96" in s) {
    const v3 = s as V3PoolState;
    snap.sqrtPriceX96 = v3.sqrtPriceX96?.toString();
    snap.tick = v3.tick;
    snap.liquidity = v3.liquidity?.toString();
    snap.tickSpacing = v3.tickSpacing;
  }
  if ("tickData" in pool && (pool as any).tickData) {
    const td = (pool as any).tickData as V3TickData;
    snap.tickDataSize = td.sortedTicks.length;
    snap.sortedTicks = td.sortedTicks;
    if ("sqrtPriceX96" in s) {
      const v3 = s as V3PoolState;
      if (v3.tick !== undefined && v3.tickSpacing) {
        snap.nearbyTicks = {};
        for (const [t, net] of td.tickMap) {
          if (Math.abs(t - v3.tick) <= v3.tickSpacing * 20) {
            snap.nearbyTicks[t.toString()] = net.toString();
          }
        }
      }
    }
  }
  return snap;
}

function rerunSwapWithDebug(pool: IPoolMath, amountIn: bigint, zeroForOne: boolean, steps: DebugSwapStep[]): void {
  if (!("tickData" in pool)) return;
  const td = (pool as any).tickData as V3TickData | null;
  if (!td || td.sortedTicks.length === 0) return;
  const v3 = pool.state as V3PoolState;
  if (!v3.sqrtPriceX96 || !v3.liquidity || v3.tick === undefined) return;
  const feePips = Math.round(pool.config.fee * 1_000_000);
  v3GetAmountOutCrossTicks(
    v3.sqrtPriceX96, v3.liquidity, v3.tick, amountIn, zeroForOne, feePips, td, steps,
  );
}

