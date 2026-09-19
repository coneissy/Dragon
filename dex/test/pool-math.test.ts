import { describe, it, expect } from "vitest";
import { V2Pool } from "@/pools/types/uniswap-v2";
import { v2GetAmountOut } from "@/pools/v2-math";
import { PoolType } from "@/pools/types";
import type { PoolConfig } from "@/pools/types";

const makeV2Config = (fee: number): PoolConfig => ({
  address: "0x0000000000000000000000000000000000000001",
  token0: "0x0000000000000000000000000000000000000002",
  token1: "0x0000000000000000000000000000000000000003",
  poolType: PoolType.UniswapV2,
  fee,
});

describe("V2 Pool Math", () => {
  it("should calculate correct output for standard 0.3% fee", () => {
    // 1 ETH in, reserves: 100 ETH / 200,000 USDC
    const amountIn = 10n ** 18n; // 1 ETH
    const reserveIn = 100n * 10n ** 18n; // 100 ETH
    const reserveOut = 200_000n * 10n ** 6n; // 200k USDC

    const out = v2GetAmountOut(amountIn, reserveIn, reserveOut, 0.003);

    // Expected: ~1970 USDC (with 0.3% fee and price impact)
    expect(out).toBeGreaterThan(1960n * 10n ** 6n);
    expect(out).toBeLessThan(2000n * 10n ** 6n);
  });

  it("should return 0 for 0 input", () => {
    const pool = new V2Pool(makeV2Config(0.003), {
      reserve0: 100n * 10n ** 18n,
      reserve1: 200_000n * 10n ** 6n,
    });
    expect(pool.getAmountOut(0n, true)).toBe(0n);
  });

  it("should return 0 for empty reserves", () => {
    const pool = new V2Pool(makeV2Config(0.003), {});
    expect(pool.getAmountOut(10n ** 18n, true)).toBe(0n);
  });

  it("should respect different fee tiers", () => {
    const reserveIn = 100n * 10n ** 18n;
    const reserveOut = 200_000n * 10n ** 6n;
    const amountIn = 10n ** 18n;

    const out30 = v2GetAmountOut(amountIn, reserveIn, reserveOut, 0.003);
    const out25 = v2GetAmountOut(amountIn, reserveIn, reserveOut, 0.0025);

    // Lower fee → higher output
    expect(out25).toBeGreaterThan(out30);
  });

  it("should handle zeroForOne correctly", () => {
    const pool = new V2Pool(makeV2Config(0.003), {
      reserve0: 100n * 10n ** 18n,
      reserve1: 200_000n * 10n ** 6n,
    });

    const outForward = pool.getAmountOut(10n ** 18n, true);
    const outReverse = pool.getAmountOut(1000n * 10n ** 6n, false);

    expect(outForward).toBeGreaterThan(0n);
    expect(outReverse).toBeGreaterThan(0n);
  });
});
