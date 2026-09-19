import {decodeErrorResult, encodeFunctionData, PublicClient} from "viem";
import {logger} from "@/utils/logger";
import {estimateGasCost} from "@/core/gas";
import {PoolType} from "@/pools/types";
import {ArbOpportunity, formatUsd} from "@/core/algos/find-opportunity-simple";
import {config, wallet} from "@/config";
import {sendTelegramMessage} from "@/utils/telegram";
import {flushDebugEntry} from "@/utils/debug-log";

const SLIPPAGE_BPS = 5n; // 0.05% max slippage

function applySlippage(amount: bigint): bigint {
  return amount * (10000n - SLIPPAGE_BPS) / 10000n;
}

export async function execute(opp: ArbOpportunity, ethPriceUsd: number, httpClient: PublicClient) {
  const calldata = encodeFunctionData({
    abi: FLASH_ARB_ABI,
    functionName: "executeArb",
    args: [
      opp.loanToken,
      opp.optimalIn,
      {
        pool: opp.pool0.address,
        poolType: poolTypeToUint8(opp.pool0.poolType),
        zeroForOne: opp.zeroForOne0,
        amountOutMin: applySlippage(opp.expectedBridgeAmount),
        feeBps: Math.round(opp.pool0.fee * 10_000),
      },
      {
        pool: opp.pool1.address,
        poolType: poolTypeToUint8(opp.pool1.poolType),
        zeroForOne: opp.zeroForOne1,
        amountOutMin: applySlippage(opp.expectedAmountOut),
        feeBps: Math.round(opp.pool1.fee * 10_000),
      },
      opp.zeroForOne0 ? opp.pool0.token1 : opp.pool0.token0, // bridgeToken
    ],
  });

  // Simulate via eth_call to get detailed revert reason
  let gasLimit: bigint;
  try {
    await httpClient.call({
      to: config.flashArbAddress,
      data: calldata,
      account: wallet.account!,
    });
  } catch (err: any) {
    let reason = err?.shortMessage ?? err?.message ?? String(err);
    // Try to decode custom error from revert data (viem nests it in cause chain)
    const revertData = err?.cause?.cause?.cause?.data ?? err?.cause?.cause?.data ?? err?.cause?.data ?? err?.data;
    if (revertData) {
      try {
        const decoded = decodeErrorResult({ abi: FLASH_ARB_ABI, data: revertData });
        reason = decoded.errorName;
      } catch {
        reason = `unknown revert: ${revertData}`;
      }
    }
    logger.info(
      {
        pool0: opp.pool0.address,
        pool1: opp.pool1.address,
        reason,
      },
      "TX simulation reverted — skipping",
    );
    if (opp.debugEntry) {
      opp.debugEntry.execution = { simulationOk: false, simulationError: reason, skippedReason: "simulation reverted" };
      flushDebugEntry(opp.debugEntry);
    }
    return;
  }

  // Estimate gas (simulation already passed, so this should succeed)
  try {
    gasLimit = await httpClient.estimateGas({
      to: config.flashArbAddress,
      data: calldata,
      account: wallet.account!,
    });
    gasLimit = (gasLimit * 110n) / 100n;
  } catch (err: any) {
    logger.warn({ reason: err?.shortMessage ?? err?.message }, "estimateGas failed after successful simulation");
    if (opp.debugEntry) {
      opp.debugEntry.execution = { simulationOk: true, skippedReason: "estimateGas failed" };
      flushDebugEntry(opp.debugEntry);
    }
    return;
  }

  // Estimate total gas cost (L2 + L1)
  const gas = await estimateGasCost(httpClient, calldata, gasLimit);

  // Check profitability
  const finalProfit = opp.netProfit - gas.totalGasCost;
  if (finalProfit <= 0n) {
    logger.info(
      {
        netProfit: opp.netProfit.toString(),
        profitUsd: formatUsd(opp.netProfit, ethPriceUsd),
        gasCost: gas.totalGasCost.toString(),
        gasCostUsd: formatUsd(gas.totalGasCost, ethPriceUsd),
      },
      "Not profitable after gas",
    );
    if (opp.debugEntry) {
      opp.debugEntry.execution = {
        simulationOk: true, gasLimit: gasLimit.toString(),
        gasCost: gas.totalGasCost.toString(), finalProfit: finalProfit.toString(),
        skippedReason: "not profitable after gas",
      };
      flushDebugEntry(opp.debugEntry);
    }
    return;
  }

  if (config.dryRun) {
    logger.info(
      {
        tokenIn: opp.loanToken,
        optimalIn: opp.optimalIn.toString(),
        netProfit: opp.netProfit.toString(),
        gasCost: gas.totalGasCost.toString(),
        finalProfit: finalProfit.toString(),
        finalProfitUsd: formatUsd(finalProfit, ethPriceUsd),
        pool0: opp.pool0.address,
        pool1: opp.pool1.address,
      },
      "DRY RUN: Would execute arb",
    );
    sendTelegramMessage(
      `<b>Arb found (simulation OK)</b>\nProfit: ${formatUsd(finalProfit, ethPriceUsd)} (after gas)\nIn: ${opp.optimalIn.toString()} ${opp.loanToken}\nPool0: <code>${opp.pool0.address}</code>\nPool1: <code>${opp.pool1.address}</code>\n<i>[DRY RUN]</i>`,
    );
    if (opp.debugEntry) {
      opp.debugEntry.execution = {
        simulationOk: true, gasLimit: gasLimit.toString(),
        gasCost: gas.totalGasCost.toString(), finalProfit: finalProfit.toString(),
        skippedReason: "dry run",
      };
      flushDebugEntry(opp.debugEntry);
    }
    return;
  }

  sendTelegramMessage(
    `<b>Arb found (simulation OK)</b>\nProfit: ${formatUsd(finalProfit, ethPriceUsd)} (after gas)\nIn: ${opp.optimalIn.toString()} ${opp.loanToken}\nPool0: <code>${opp.pool0.address}</code>\nPool1: <code>${opp.pool1.address}</code>`,
  );

  // Send transaction
  try {
    const hash = await wallet.sendTransaction({
      to: config.flashArbAddress,
      data: calldata,
      gas: gasLimit,
      chain: null,
      account: wallet.account!,
    });

    logger.info(
      {
        hash,
        finalProfit: finalProfit.toString(),
        finalProfitUsd: formatUsd(finalProfit, ethPriceUsd),
        gasLimit: gasLimit.toString(),
      },
      "Arb TX submitted",
    );
    sendTelegramMessage(
      `<b>TX submitted</b>\nHash: <code>${hash}</code>\nExpected profit: ${formatUsd(finalProfit, ethPriceUsd)}`,
    );

    // Wait for receipt
    const receipt = await httpClient.waitForTransactionReceipt({ hash });
    if (receipt.status === "success") {
      logger.info(
        { hash, gasUsed: receipt.gasUsed.toString() },
        "Arb TX confirmed",
      );
      sendTelegramMessage(
        `<b>TX confirmed</b>\nHash: <code>${hash}</code>\nGas used: ${receipt.gasUsed.toString()}`,
      );
      if (opp.debugEntry) {
        opp.debugEntry.execution = {
          simulationOk: true, gasLimit: gasLimit.toString(),
          gasCost: gas.totalGasCost.toString(), finalProfit: finalProfit.toString(),
          txHash: hash, txStatus: "success",
        };
        flushDebugEntry(opp.debugEntry);
      }
    } else {
      logger.warn({ hash }, "Arb TX reverted on-chain");
      sendTelegramMessage(
        `<b>⚠ TX REVERTED</b>\nHash: <code>${hash}</code>`,
      );
      if (opp.debugEntry) {
        opp.debugEntry.execution = {
          simulationOk: true, gasLimit: gasLimit.toString(),
          gasCost: gas.totalGasCost.toString(), finalProfit: finalProfit.toString(),
          txHash: hash, txStatus: "reverted",
        };
        flushDebugEntry(opp.debugEntry);
      }
    }
  } catch (err) {
    logger.error({ err }, "Failed to send arb TX");
    if (opp.debugEntry) {
      opp.debugEntry.execution = {
        simulationOk: true, gasLimit: gasLimit.toString(),
        gasCost: gas.totalGasCost.toString(), finalProfit: finalProfit.toString(),
        skippedReason: `send failed: ${err}`,
      };
      flushDebugEntry(opp.debugEntry);
    }
  }
};


const SWAP_PARAMS_COMPONENTS = [
  { name: "pool", type: "address" },
  { name: "poolType", type: "uint8" },
  { name: "zeroForOne", type: "bool" },
  { name: "amountOutMin", type: "uint256" },
  { name: "feeBps", type: "uint24" },
] as const;

const FLASH_ARB_ABI = [
  { type: "error", name: "NotOwner", inputs: [] },
  { type: "error", name: "NotAave", inputs: [] },
  { type: "error", name: "NotSelf", inputs: [] },
  { type: "error", name: "NoProfit", inputs: [] },
  { type: "error", name: "Slippage", inputs: [] },
  { type: "error", name: "UnknownPoolType", inputs: [] },
  { type: "error", name: "UnauthorizedCallback", inputs: [] },
  {
    inputs: [
      { name: "token", type: "address" },
      { name: "amount", type: "uint256" },
      {
        components: SWAP_PARAMS_COMPONENTS,
        name: "swap0",
        type: "tuple",
      },
      {
        components: SWAP_PARAMS_COMPONENTS,
        name: "swap1",
        type: "tuple",
      },
      { name: "bridgeToken", type: "address" },
    ],
    name: "executeArb",
    outputs: [],
    stateMutability: "nonpayable",
    type: "function",
  },
] as const;

// Map pool type to contract enum
function poolTypeToUint8(pt: PoolType): number {
  switch (pt) {
    case PoolType.UniswapV2:
      return 0; // POOL_V2
    case PoolType.UniswapV3:
    case PoolType.PancakeV3:
      return 1; // POOL_V3
    case PoolType.AerodromeV2:
      return 2; // POOL_V2_AERO
  }
}
