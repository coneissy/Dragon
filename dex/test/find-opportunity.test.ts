import { describe, it, expect } from "vitest";
import type { Address } from "viem";
import { findOpportunitySimple } from "@/core/algos/find-opportunity-simple";
import { V2Pool } from "@/pools/types/uniswap-v2";
import { PoolType } from "@/pools/types";
import type { PoolConfig } from "@/pools/types";

const TOKEN_A = "0x0000000000000000000000000000000000000aaa" as Address;
const TOKEN_B = "0x0000000000000000000000000000000000000bbb" as Address;

const POOL_0 = "0x0000000000000000000000000000000000000001" as Address;
const POOL_1 = "0x0000000000000000000000000000000000000002" as Address;

function makePool(
  address: Address,
  reserve0: bigint,
  reserve1: bigint,
  fee = 0.003,
): V2Pool {
  const config: PoolConfig = {
    address,
    token0: TOKEN_A,
    token1: TOKEN_B,
    poolType: PoolType.UniswapV2,
    fee,
  };
  return new V2Pool(config, { reserve0, reserve1 });
}

const ETH = 10n ** 18n;

describe("findOpportunitySimple", () => {
  it("finds an opportunity when prices diverge", () => {
    // pool0: 1 A = 2000 B, pool1: 1 A = 2100 B (5% spread)
    const pool0 = makePool(POOL_0, 1000n * ETH, 2_000_000n * ETH);
    const pool1 = makePool(POOL_1, 1000n * ETH, 2_100_000n * ETH);

    const opp = findOpportunitySimple([pool0, pool1]);

    expect(opp).toBeDefined();
    expect(opp!.netProfit).toBeGreaterThan(0n);
    expect(opp!.loanToken).toBe(TOKEN_A);
    // Buy B where it's cheaper relative to A: pool1 gives more B per A
    expect(opp!.pool0.address).toBe(POOL_1);
    expect(opp!.pool1.address).toBe(POOL_0);
  });

  it("returns undefined for balanced pools", () => {
    const pool0 = makePool(POOL_0, 1000n * ETH, 2_000_000n * ETH);
    const pool1 = makePool(POOL_1, 1000n * ETH, 2_000_000n * ETH);

    expect(findOpportunitySimple([pool0, pool1])).toBeUndefined();
  });

  it("returns undefined when spread is smaller than fees", () => {
    // 0.01% spread vs 2 × 0.3% swap fee + 0.05% flashloan fee
    const pool0 = makePool(POOL_0, 1000n * ETH, 2_000_000n * ETH);
    const pool1 = makePool(POOL_1, 1000n * ETH, 2_000_200n * ETH);

    expect(findOpportunitySimple([pool0, pool1])).toBeUndefined();
  });

  it("accounts for swap output in both legs", () => {
    const pool0 = makePool(POOL_0, 1000n * ETH, 2_000_000n * ETH);
    const pool1 = makePool(POOL_1, 1000n * ETH, 2_100_000n * ETH);

    const opp = findOpportunitySimple([pool0, pool1])!;

    // Replay the two swaps and verify reported amounts
    const cheap = opp.pool0.address === POOL_1 ? pool1 : pool0;
    const dear = opp.pool1.address === POOL_0 ? pool0 : pool1;
    const bridge = cheap.getAmountOut(opp.optimalIn, true);
    const out = dear.getAmountOut(bridge, false);

    expect(opp.expectedBridgeAmount).toBe(bridge);
    expect(opp.expectedAmountOut).toBe(out);
    // netProfit = out - in - flashloan fee (5 bps)
    const flashFee = (opp.optimalIn * 5n) / 10000n;
    expect(opp.netProfit).toBe(out - opp.optimalIn - flashFee);
  });

  it("picks the most profitable pair among many pools", () => {
    const pool0 = makePool(POOL_0, 1000n * ETH, 2_000_000n * ETH);
    const pool1 = makePool(POOL_1, 1000n * ETH, 2_100_000n * ETH);
    const pool2 = makePool(
      "0x0000000000000000000000000000000000000003" as Address,
      1000n * ETH,
      2_300_000n * ETH, // 15% spread vs pool0 — best opportunity
    );

    const opp = findOpportunitySimple([pool0, pool1, pool2])!;

    expect(opp.pool0.address).toBe(pool2.config.address);
  });
});
