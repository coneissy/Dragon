import type { PublicClient } from "viem";
import { logger } from "@/utils/logger";

// Base L1 gas oracle (predeploy on all OP Stack L2s)
const GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F" as const;

const GAS_ORACLE_ABI = [
  {
    inputs: [{ name: "_data", type: "bytes" }],
    name: "getL1Fee",
    outputs: [{ name: "", type: "uint256" }],
    stateMutability: "view",
    type: "function",
  },
] as const;

export interface GasEstimate {
  l2GasCost: bigint;
  l1DataFee: bigint;
  totalGasCost: bigint;
}

/**
 * Estimate total gas cost for a transaction on Base L2.
 * Total = L2 execution gas * gasPrice + L1 data posting fee.
 */
export async function estimateGasCost(
  client: PublicClient,
  txData: `0x${string}`,
  gasLimit: bigint,
): Promise<GasEstimate> {
  // L2 gas price
  const gasPrice = await client.getGasPrice();
  const l2GasCost = gasLimit * gasPrice;

  // L1 data posting fee via GasPriceOracle
  let l1DataFee = 0n;
  try {
    const result = await client.readContract({
      address: GAS_PRICE_ORACLE,
      abi: GAS_ORACLE_ABI,
      functionName: "getL1Fee",
      args: [txData],
    });
    l1DataFee = result;
  } catch (err) {
    logger.warn("Failed to get L1 fee, using estimate");
    // Fallback: estimate ~2000 gas units * L1 gas price (~30 gwei)
    l1DataFee = 2000n * 30_000_000_000n;
  }

  const totalGasCost = l2GasCost + l1DataFee;

  logger.debug(
    {
      l2GasCost: l2GasCost.toString(),
      l1DataFee: l1DataFee.toString(),
      totalGasCost: totalGasCost.toString(),
    },
    "Gas estimate",
  );

  return { l2GasCost, l1DataFee, totalGasCost };
}
