/**
 * Off-chain V3 swap math with tick crossing.
 * Ported from Uniswap V3 core contracts (TickMath, SqrtPriceMath, SwapMath).
 * Pure bigint arithmetic, no external dependencies.
 */

import type { V3TickData } from "@/pools/types";
import type { DebugSwapStep } from "@/utils/debug-log";

const Q96 = 1n << 96n;
const Q128 = 1n << 128n;
const MAX_UINT160 = (1n << 160n) - 1n;
const MAX_UINT256 = (1n << 256n) - 1n;

// Price limits (from TickMath.sol)
const MIN_SQRT_RATIO = 4295128739n;
const MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342n;

// ── TickMath ──────────────────────────────────────────────────────────

/**
 * Port of TickMath.getSqrtRatioAtTick.
 * Returns sqrtPriceX96 for a given tick.
 */
export function getSqrtPriceAtTick(tick: number): bigint {
  const absTick = tick < 0 ? -tick : tick;
  if (absTick > 887272) throw new Error("tick out of range");

  let ratio: bigint =
    (absTick & 0x1) !== 0
      ? 0xfffcb933bd6fad37aa2d162d1a594001n
      : 0x100000000000000000000000000000000n;
  if ((absTick & 0x2) !== 0) ratio = (ratio * 0xfff97272373d413259a46990580e213an) >> 128n;
  if ((absTick & 0x4) !== 0) ratio = (ratio * 0xfff2e50f5f656932ef12357cf3c7fdccn) >> 128n;
  if ((absTick & 0x8) !== 0) ratio = (ratio * 0xffe5caca7e10e4e61c3624eaa0941cd0n) >> 128n;
  if ((absTick & 0x10) !== 0) ratio = (ratio * 0xffcb9843d60f6159c9db58835c926644n) >> 128n;
  if ((absTick & 0x20) !== 0) ratio = (ratio * 0xff973b41fa98c081472e6896dfb254c0n) >> 128n;
  if ((absTick & 0x40) !== 0) ratio = (ratio * 0xff2ea16466c96a3843ec78b326b52861n) >> 128n;
  if ((absTick & 0x80) !== 0) ratio = (ratio * 0xfe5dee046a99a2a811c461f1969c3053n) >> 128n;
  if ((absTick & 0x100) !== 0) ratio = (ratio * 0xfcbe86c7900a88aedcffc83b479aa3a4n) >> 128n;
  if ((absTick & 0x200) !== 0) ratio = (ratio * 0xf987a7253ac413176f2b074cf7815e54n) >> 128n;
  if ((absTick & 0x400) !== 0) ratio = (ratio * 0xf3392b0822b70005940c7a398e4b70f3n) >> 128n;
  if ((absTick & 0x800) !== 0) ratio = (ratio * 0xe7159475a2c29b7443b29c7fa6e889d9n) >> 128n;
  if ((absTick & 0x1000) !== 0) ratio = (ratio * 0xd097f3bdfd2022b8845ad8f792aa5825n) >> 128n;
  if ((absTick & 0x2000) !== 0) ratio = (ratio * 0xa9f746462d870fdf8a65dc1f90e061e5n) >> 128n;
  if ((absTick & 0x4000) !== 0) ratio = (ratio * 0x70d869a156d2a1b890bb3df62baf32f7n) >> 128n;
  if ((absTick & 0x8000) !== 0) ratio = (ratio * 0x31be135f97d08fd981231505542fcfa6n) >> 128n;
  if ((absTick & 0x10000) !== 0) ratio = (ratio * 0x9aa508b5b7a84e1c677de54f3e99bc9n) >> 128n;
  if ((absTick & 0x20000) !== 0) ratio = (ratio * 0x5d6af8dedb81196699c329225ee604n) >> 128n;
  if ((absTick & 0x40000) !== 0) ratio = (ratio * 0x2216e584f5fa1ea926041bedfe98n) >> 128n;
  if ((absTick & 0x80000) !== 0) ratio = (ratio * 0x48a170391f7dc42444e8fa2n) >> 128n;

  if (tick > 0) ratio = MAX_UINT256 / ratio;

  // Shift down to Q96
  return (ratio >> 32n) + (ratio % (1n << 32n) === 0n ? 0n : 1n);
}

/**
 * Port of TickMath.getTickAtSqrtRatio.
 * Returns the greatest tick whose sqrtPrice <= sqrtPriceX96.
 */
export function getTickAtSqrtPrice(sqrtPriceX96: bigint): number {
  if (sqrtPriceX96 < MIN_SQRT_RATIO || sqrtPriceX96 >= MAX_SQRT_RATIO) {
    throw new Error("sqrtPriceX96 out of range");
  }

  let ratio = sqrtPriceX96 << 32n;
  let r = ratio;
  let msb = 0n;

  let f = r > 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFn ? 1n : 0n;
  msb = msb | (f << 7n);
  r = r >> (f * 128n);

  f = r > 0xFFFFFFFFFFFFFFFFn ? 1n : 0n;
  msb = msb | (f << 6n);
  r = r >> (f * 64n);

  f = r > 0xFFFFFFFFn ? 1n : 0n;
  msb = msb | (f << 5n);
  r = r >> (f * 32n);

  f = r > 0xFFFFn ? 1n : 0n;
  msb = msb | (f << 4n);
  r = r >> (f * 16n);

  f = r > 0xFFn ? 1n : 0n;
  msb = msb | (f << 3n);
  r = r >> (f * 8n);

  f = r > 0xFn ? 1n : 0n;
  msb = msb | (f << 2n);
  r = r >> (f * 4n);

  f = r > 0x3n ? 1n : 0n;
  msb = msb | (f << 1n);
  r = r >> (f * 2n);

  f = r > 0x1n ? 1n : 0n;
  msb = msb | f;

  if (msb >= 128n) {
    r = ratio >> (msb - 127n);
  } else {
    r = ratio << (127n - msb);
  }

  // log2 refinement (14 iterations)
  let log2 = (msb - 128n) << 64n;

  for (let i = 0; i < 14; i++) {
    r = (r * r) >> 127n;
    const f2 = r >> 128n;
    log2 = log2 | (f2 << BigInt(63 - i));
    r = r >> f2;
  }

  // log_sqrt10001 = log2 * 255738958999603826347141
  const logSqrt10001 = log2 * 255738958999603826347141n;

  const tickLow = Number(
    (logSqrt10001 - 3402992956809132418596140100660247210n) >> 128n,
  );
  const tickHigh = Number(
    (logSqrt10001 + 291339464771989622907027621153398088495n) >> 128n,
  );

  if (tickLow === tickHigh) return tickLow;
  return getSqrtPriceAtTick(tickHigh) <= sqrtPriceX96 ? tickHigh : tickLow;
}

// ── SqrtPriceMath ─────────────────────────────────────────────────────

function mulDiv(a: bigint, b: bigint, denominator: bigint): bigint {
  return (a * b) / denominator;
}

function mulDivRoundingUp(a: bigint, b: bigint, denominator: bigint): bigint {
  const product = a * b;
  const result = product / denominator;
  if (product % denominator !== 0n) return result + 1n;
  return result;
}

function divRoundingUp(a: bigint, b: bigint): bigint {
  const result = a / b;
  if (a % b !== 0n) return result + 1n;
  return result;
}

export function getNextSqrtPriceFromAmount0RoundingUp(
  sqrtPriceX96: bigint,
  liquidity: bigint,
  amount: bigint,
  add: boolean,
): bigint {
  if (amount === 0n) return sqrtPriceX96;
  const numerator1 = liquidity << 96n;

  if (add) {
    const product = amount * sqrtPriceX96;
    const denominator = numerator1 + product;
    if (denominator >= numerator1) {
      return mulDivRoundingUp(numerator1, sqrtPriceX96, denominator);
    }
    // Overflow path
    return mulDivRoundingUp(numerator1, 1n, numerator1 / sqrtPriceX96 + amount);
  } else {
    const product = amount * sqrtPriceX96;
    const denominator = numerator1 - product;
    return mulDivRoundingUp(numerator1, sqrtPriceX96, denominator);
  }
}

export function getNextSqrtPriceFromAmount1RoundingDown(
  sqrtPriceX96: bigint,
  liquidity: bigint,
  amount: bigint,
  add: boolean,
): bigint {
  if (add) {
    const quotient = (amount * Q96) / liquidity;
    return sqrtPriceX96 + quotient;
  } else {
    const quotient = mulDivRoundingUp(amount, Q96, liquidity);
    if (sqrtPriceX96 <= quotient) throw new Error("sqrtPrice underflow");
    return sqrtPriceX96 - quotient;
  }
}

export function getAmount0Delta(
  sqrtPriceA: bigint,
  sqrtPriceB: bigint,
  liquidity: bigint,
  roundUp: boolean = true,
): bigint {
  if (sqrtPriceA > sqrtPriceB) [sqrtPriceA, sqrtPriceB] = [sqrtPriceB, sqrtPriceA];
  const numerator1 = liquidity << 96n;
  const numerator2 = sqrtPriceB - sqrtPriceA;
  if (roundUp) {
    return divRoundingUp(
      mulDivRoundingUp(numerator1, numerator2, sqrtPriceB),
      sqrtPriceA,
    );
  }
  return mulDiv(numerator1, numerator2, sqrtPriceB) / sqrtPriceA;
}

export function getAmount1Delta(
  sqrtPriceA: bigint,
  sqrtPriceB: bigint,
  liquidity: bigint,
  roundUp: boolean = true,
): bigint {
  if (sqrtPriceA > sqrtPriceB) [sqrtPriceA, sqrtPriceB] = [sqrtPriceB, sqrtPriceA];
  if (roundUp) {
    return mulDivRoundingUp(liquidity, sqrtPriceB - sqrtPriceA, Q96);
  }
  return mulDiv(liquidity, sqrtPriceB - sqrtPriceA, Q96);
}

// ── SwapMath ──────────────────────────────────────────────────────────

export interface SwapStepResult {
  sqrtPriceNext: bigint;
  amountIn: bigint;
  amountOut: bigint;
  feeAmount: bigint;
}

/**
 * Port of SwapMath.computeSwapStep.
 */
export function computeSwapStep(
  sqrtPriceCurrent: bigint,
  sqrtPriceTarget: bigint,
  liquidity: bigint,
  amountRemaining: bigint,
  feePips: bigint,
): SwapStepResult {
  const zeroForOne = sqrtPriceCurrent >= sqrtPriceTarget;
  const exactIn = amountRemaining >= 0n;

  let sqrtPriceNext: bigint;
  let amountIn = 0n;
  let amountOut = 0n;
  let feeAmount = 0n;

  if (exactIn) {
    const amountRemainingLessFee =
      mulDiv(amountRemaining, 1000000n - feePips, 1000000n);

    amountIn = zeroForOne
      ? getAmount0Delta(sqrtPriceTarget, sqrtPriceCurrent, liquidity, true)
      : getAmount1Delta(sqrtPriceCurrent, sqrtPriceTarget, liquidity, true);

    if (amountRemainingLessFee >= amountIn) {
      sqrtPriceNext = sqrtPriceTarget;
    } else {
      sqrtPriceNext = zeroForOne
        ? getNextSqrtPriceFromAmount0RoundingUp(sqrtPriceCurrent, liquidity, amountRemainingLessFee, true)
        : getNextSqrtPriceFromAmount1RoundingDown(sqrtPriceCurrent, liquidity, amountRemainingLessFee, true);
    }
  } else {
    amountOut = zeroForOne
      ? getAmount1Delta(sqrtPriceTarget, sqrtPriceCurrent, liquidity, false)
      : getAmount0Delta(sqrtPriceCurrent, sqrtPriceTarget, liquidity, false);

    if (-amountRemaining >= amountOut) {
      sqrtPriceNext = sqrtPriceTarget;
    } else {
      sqrtPriceNext = zeroForOne
        ? getNextSqrtPriceFromAmount1RoundingDown(sqrtPriceCurrent, liquidity, -amountRemaining, false)
        : getNextSqrtPriceFromAmount0RoundingUp(sqrtPriceCurrent, liquidity, -amountRemaining, false);
    }
  }

  const max = sqrtPriceTarget === sqrtPriceNext;

  // Recalculate amounts — match Solidity's `max && exactIn` / `max && !exactIn` logic
  if (zeroForOne) {
    amountIn = max && exactIn ? amountIn : getAmount0Delta(sqrtPriceNext, sqrtPriceCurrent, liquidity, true);
    amountOut = max && !exactIn ? amountOut : getAmount1Delta(sqrtPriceNext, sqrtPriceCurrent, liquidity, false);
  } else {
    amountIn = max && exactIn ? amountIn : getAmount1Delta(sqrtPriceCurrent, sqrtPriceNext, liquidity, true);
    amountOut = max && !exactIn ? amountOut : getAmount0Delta(sqrtPriceCurrent, sqrtPriceNext, liquidity, false);
  }

  // Cap output for exactOut case
  if (!exactIn && amountOut > -amountRemaining) {
    amountOut = -amountRemaining;
  }

  if (exactIn && sqrtPriceNext !== sqrtPriceTarget) {
    // Didn't reach target — all remaining is fee
    feeAmount = amountRemaining - amountIn;
  } else {
    feeAmount = mulDivRoundingUp(amountIn, feePips, 1000000n - feePips);
  }

  return { sqrtPriceNext, amountIn, amountOut, feeAmount };
}

// ── Tick Lookup ───────────────────────────────────────────────────────

/**
 * Find the next initialized tick via binary search.
 * For zeroForOne: find the greatest initialized tick <= currentTick
 * For !zeroForOne: find the smallest initialized tick > currentTick
 */
export function findNextInitializedTick(
  tickData: V3TickData,
  currentTick: number,
  zeroForOne: boolean,
): number | null {
  const { sortedTicks } = tickData;
  if (sortedTicks.length === 0) return null;

  if (zeroForOne) {
    // Find greatest tick <= currentTick
    let lo = 0;
    let hi = sortedTicks.length - 1;
    if (sortedTicks[0] > currentTick) return null;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (sortedTicks[mid] <= currentTick) lo = mid;
      else hi = mid - 1;
    }
    return sortedTicks[lo];
  } else {
    // Find smallest tick > currentTick
    let lo = 0;
    let hi = sortedTicks.length - 1;
    if (sortedTicks[hi] <= currentTick) return null;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (sortedTicks[mid] > currentTick) hi = mid;
      else lo = mid + 1;
    }
    return sortedTicks[lo];
  }
}

// ── Main Swap Loop ────────────────────────────────────────────────────

/**
 * Full V3 swap with tick crossing. Port of IUniswapV3Pool.swap() loop.
 * Returns totalAmountOut for exactInput swaps.
 */
export function v3GetAmountOutCrossTicks(
  sqrtPriceX96: bigint,
  liquidity: bigint,
  tick: number,
  amountIn: bigint,
  zeroForOne: boolean,
  feePips: number,
  tickData: V3TickData,
  debugSteps?: DebugSwapStep[],
): bigint {
  if (liquidity <= 0n || amountIn <= 0n || sqrtPriceX96 <= 0n) return 0n;

  const feePipsBn = BigInt(feePips);
  const sqrtPriceLimit = zeroForOne ? MIN_SQRT_RATIO + 1n : MAX_SQRT_RATIO - 1n;

  let sqrtPriceCurrent = sqrtPriceX96;
  let currentTick = tick;
  let currentLiquidity = liquidity;
  let amountRemaining = amountIn;
  let totalAmountOut = 0n;

  // Safety: max 500 iterations to prevent infinite loops
  for (let i = 0; i < 500 && amountRemaining > 0n; i++) {
    // Find next initialized tick
    const nextTick = findNextInitializedTick(tickData, currentTick, zeroForOne);

    // Determine sqrtPriceTarget
    let sqrtPriceTarget: bigint;
    if (nextTick === null) {
      // No more initialized ticks in range — use price limit
      sqrtPriceTarget = sqrtPriceLimit;
    } else {
      sqrtPriceTarget = getSqrtPriceAtTick(nextTick);
      // Clamp to price limit
      if (zeroForOne && sqrtPriceTarget < sqrtPriceLimit) {
        sqrtPriceTarget = sqrtPriceLimit;
      } else if (!zeroForOne && sqrtPriceTarget > sqrtPriceLimit) {
        sqrtPriceTarget = sqrtPriceLimit;
      }
    }

    // Compute swap step
    const step = computeSwapStep(
      sqrtPriceCurrent,
      sqrtPriceTarget,
      currentLiquidity,
      amountRemaining,
      feePipsBn,
    );

    sqrtPriceCurrent = step.sqrtPriceNext;
    amountRemaining -= step.amountIn + step.feeAmount;
    totalAmountOut += step.amountOut;

    // Check if we've reached the tick boundary and need to cross
    const crossedTick = sqrtPriceCurrent === sqrtPriceTarget && nextTick !== null;
    let newLiquidity: bigint | undefined;
    if (crossedTick) {
      // Cross the tick — adjust liquidity
      const liquidityNet = tickData.tickMap.get(nextTick) ?? 0n;
      if (zeroForOne) {
        currentLiquidity -= liquidityNet;
        currentTick = nextTick - 1;
      } else {
        currentLiquidity += liquidityNet;
        currentTick = nextTick;
      }
      newLiquidity = currentLiquidity;
    } else {
      currentTick = getTickAtSqrtPrice(sqrtPriceCurrent);
    }

    if (debugSteps) {
      const liquidityNet = nextTick !== null ? tickData.tickMap.get(nextTick) : undefined;
      debugSteps.push({
        iteration: i,
        currentTick,
        sqrtPriceCurrent: sqrtPriceCurrent.toString(),
        nextTick,
        sqrtPriceTarget: sqrtPriceTarget.toString(),
        liquidity: currentLiquidity.toString(),
        amountRemaining: amountRemaining.toString(),
        step: {
          sqrtPriceNext: step.sqrtPriceNext.toString(),
          amountIn: step.amountIn.toString(),
          amountOut: step.amountOut.toString(),
          feeAmount: step.feeAmount.toString(),
        },
        crossedTick,
        liquidityNet: liquidityNet?.toString(),
        newLiquidity: newLiquidity?.toString(),
      });
    }

    // If we've hit the price limit, stop
    if (sqrtPriceCurrent === sqrtPriceLimit) break;
  }

  return totalAmountOut;
}

// ── Legacy single-tick fallback ───────────────────────────────────────

/**
 * Calculate amountOut for a V3 swap within the current tick.
 * Accurate as long as the swap doesn't cross a tick boundary.
 * Kept as fallback when tick data is unavailable.
 */
export function v3GetAmountOut(
  sqrtPriceX96: bigint,
  liquidity: bigint,
  amountIn: bigint,
  zeroForOne: boolean,
  fee: number,
): bigint {
  if (liquidity <= 0n || amountIn <= 0n || sqrtPriceX96 <= 0n) return 0n;

  const FEE_DENOM = 1_000_000n;
  const feeScaled = BigInt(Math.round(fee * 1_000_000));
  const amountInAfterFee = amountIn * (FEE_DENOM - feeScaled) / FEE_DENOM;

  if (zeroForOne) {
    const lShifted = liquidity << 96n;
    const denominator = lShifted + amountInAfterFee * sqrtPriceX96;
    const sqrtPriceX96After = lShifted * sqrtPriceX96 / denominator;
    return liquidity * (sqrtPriceX96 - sqrtPriceX96After) / Q96;
  } else {
    const sqrtPriceX96After = sqrtPriceX96 + amountInAfterFee * Q96 / liquidity;
    const diff = sqrtPriceX96After - sqrtPriceX96;
    return (liquidity << 96n) * diff / sqrtPriceX96After / sqrtPriceX96;
  }
}
