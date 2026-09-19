import { mkdirSync, writeFileSync } from "fs";
import { config } from "@/config";

export interface DebugSwapStep {
  iteration: number;
  currentTick: number;
  sqrtPriceCurrent: string;
  nextTick: number | null;
  sqrtPriceTarget: string;
  liquidity: string;
  amountRemaining: string;
  step: {
    sqrtPriceNext: string;
    amountIn: string;
    amountOut: string;
    feeAmount: string;
  };
  crossedTick: boolean;
  liquidityNet?: string;
  newLiquidity?: string;
}

export interface DebugPoolSnapshot {
  address: string;
  poolType: string;
  fee: number;
  label?: string;
  // V2
  reserve0?: string;
  reserve1?: string;
  // V3
  sqrtPriceX96?: string;
  tick?: number;
  liquidity?: string;
  tickSpacing?: number;
  tickDataSize?: number;
  sortedTicks?: number[];
  nearbyTicks?: Record<string, string>;
}

export interface DebugEntry {
  timestamp: string;
  pool0Snapshot: DebugPoolSnapshot;
  pool1Snapshot: DebugPoolSnapshot;
  loanToken: string;
  maxFlash: string;
  searchSteps: { lo: string; hi: string; mid1: string; mid2: string; p1: string; p2: string }[];
  optimalIn: string;
  swap0Steps?: DebugSwapStep[];
  swap1Steps?: DebugSwapStep[];
  swap0Result: string;
  swap1Result: string;
  flashloanFee: string;
  netProfit: string;
  execution?: {
    simulationOk: boolean;
    simulationError?: string;
    gasLimit?: string;
    gasCost?: string;
    finalProfit?: string;
    txHash?: string;
    txStatus?: string;
    skippedReason?: string;
  };
}

let currentEntry: DebugEntry | null = null;

export function isDebugEnabled(): boolean {
  return config.debugLog;
}

export function startDebugEntry(): DebugEntry | null {
  if (!config.debugLog) return null;
  currentEntry = {} as DebugEntry;
  currentEntry.timestamp = new Date().toISOString();
  currentEntry.searchSteps = [];
  return currentEntry;
}

export function getCurrentDebugEntry(): DebugEntry | null {
  return currentEntry;
}

export function flushDebugEntry(entry: DebugEntry): void {
  if (!config.debugLog || !entry) return;
  try {
    mkdirSync("logs", { recursive: true });
    const ts = entry.timestamp.replace(/[:.]/g, "-");
    const p0 = entry.pool0Snapshot?.address?.slice(0, 10) ?? "unknown";
    const p1 = entry.pool1Snapshot?.address?.slice(0, 10) ?? "unknown";
    const filename = `logs/${ts}_${p0}_${p1}.json`;
    writeFileSync(filename, JSON.stringify(entry, null, 2));
  } catch { /* non-critical */ }
  if (currentEntry === entry) currentEntry = null;
}
