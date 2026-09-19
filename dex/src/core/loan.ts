import type {Address} from "viem";
import {tokenMap} from "@/config/tokens";

const maxFlashCache = new Map<string, bigint>();

function defaultMaxFlash(decimals: number): bigint {
  if (decimals <= 8) {
    // Stablecoins (USDC, USDbC, USDT) — 500k units
    return 500_000n * 10n ** BigInt(decimals);
  }
  // 18-decimal tokens (WETH, DAI, etc.) — 100 units
  return 100n * 10n ** BigInt(decimals);
}

export function getMaxFlash(tokenAddress: Address): bigint {
  const key = tokenAddress.toLowerCase();
  const cached = maxFlashCache.get(key);
  if (cached !== undefined) return cached;

  const token = tokenMap.get(key as Address);
  const maxFlash = defaultMaxFlash(token?.decimals ?? 18);
  maxFlashCache.set(key, maxFlash);
  return maxFlash;
}
