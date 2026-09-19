# DEX Arbitrage Bot

Atomic arbitrage bot for [Base](https://base.org). Watches AMM pools across multiple DEXes in real time, finds price discrepancies between pools trading the same pair, and executes the arbitrage in a single transaction using Aave V3 flashloans — no upfront capital required.

```
   Aave V3 flashloan (WETH)
        │
        ▼
   Pool A: WETH ──► USDC      (buy on the cheaper pool)
        │
        ▼
   Pool B: USDC ──► WETH      (sell on the pricier pool)
        │
        ▼
   repay loan + 0.05% fee, keep the profit
```

![Dashboard — live pool prices and spreads](assets/dashboard.jpg)

## How it works

1. **Listen.** Subscribes via WebSocket to `Sync` (V2) and `Swap` (V3) events for ~20 pools across Uniswap, SushiSwap, PancakeSwap, Aerodrome, AlienBase and others. Pool state (reserves / sqrtPrice, liquidity, ticks) is kept in memory and updated from events — no polling.
2. **Simulate.** On every state change, all pool pairs in the group are checked in both directions. Swap outputs are computed locally with exact AMM math:
   - constant-product formula for V2 pools,
   - full Uniswap V3 math with **tick crossing** (`sqrtPriceX96` movement, liquidity changes at initialized ticks via `tickBitmap`) — a TypeScript port of the core `SwapMath`, so quotes stay accurate even when a swap walks through multiple ticks.
3. **Optimize.** The optimal input amount is found by ternary search over the profit function (profit is unimodal in input size for a two-pool arb), capped by available flashloan liquidity.
4. **Execute.** If net profit (after flashloan fee and gas estimate) is positive, the bot calls the `FlashArb` contract, which takes an Aave V3 flashloan, performs both swaps, and reverts if the output doesn't cover the loan plus premium — so a failed arb only costs gas.

The whole cycle from event to submitted transaction happens within a single block time.

## Components

| Component | Description |
| --- | --- |
| `src/` | TypeScript bot: event listeners, local AMM math, opportunity finder, executor |
| `contracts/` | Solidity (Foundry): `FlashArb.sol` — flashloan receiver executing the swap chain atomically |
| `frontend/` | React dashboard: live pool prices and spreads over WebSocket |

### Supported pool types

- Uniswap V2 and forks (SushiSwap, QuickSwap, SwapBased)
- Aerodrome V2 (volatile pairs)
- Uniswap V3 and forks (Aerodrome Slipstream, SushiSwap V3, AlienBase V3)
- PancakeSwap V3 (separate callback signature)

Pool fees are read on-chain at startup, which also handles dynamic-fee pools like Aerodrome.

## Stack

**Bot:** TypeScript, [viem](https://viem.sh), pino, ws
**Contracts:** Solidity 0.8, Foundry, Aave V3
**Dashboard:** React 19, Vite

## Running

### 1. Deploy the contract

```bash
cd contracts
forge build
forge script script/Deploy.s.sol --rpc-url $BASE_RPC_HTTP --broadcast
```

### 2. Configure

```bash
cp .env.example .env
```

| Variable | Description |
| --- | --- |
| `BASE_RPC_HTTP` | HTTP RPC endpoint (Base mainnet) |
| `BASE_RPC_WS` | WebSocket RPC endpoint (low latency matters) |
| `PRIVATE_KEY` | Executor wallet key |
| `FLASH_ARB_ADDRESS` | Deployed `FlashArb` contract address |
| `DRY_RUN` | `true` — find and log opportunities without sending transactions |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | Optional Telegram notifications on executions |

Traded pairs and pools are configured in `src/config/pools.ts`.

### 3. Run

```bash
npm install
npm start
```

Dashboard (optional):

```bash
cd frontend && npm install && npm run dev
```

The bot serves pool state on `ws://localhost:3001`; the dashboard renders live prices and spreads per pool group.

## Design notes

- **Local math instead of `eth_call` quoting.** Quoting via RPC is too slow to compete within a block. All swap simulation runs in-process on locally maintained state; the chain is only touched to send the final transaction.
- **Profit check on-chain as the last line of defense.** Off-chain estimates can go stale by execution time; `FlashArb` reverts if the final balance doesn't cover the loan plus premium, bounding the worst case to the gas cost.
- **Slippage-bounded swaps.** Each leg gets an `amountOutMin` derived from the simulated output, protecting against state changes between simulation and execution.
- **`bigint` everywhere.** All math uses native bigints with the same fixed-point conventions as the on-chain contracts (Q96 for prices), avoiding floating-point drift.

## Disclaimer

Educational project. On-chain arbitrage is a highly competitive, adversarial environment — running this with real funds will most likely lose money to gas and better-positioned searchers. Use at your own risk.
