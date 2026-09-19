export interface PoolToken {
  symbol: string;
  address: string;
  decimals: number;
}

export interface PoolData {
  address: string;
  token0: PoolToken;
  token1: PoolToken;
  poolType: string;
  fee: number;
  label?: string;
  reserve0?: string;
  reserve1?: string;
  sqrtPriceX96?: string;
  tick?: number;
  liquidity?: string;
  price0?: number;
  price1?: number;
  tvlQuote?: number;
  lastUpdate: number;
}

export interface PoolGroup {
  name: string;
  pools: PoolData[];
}

export type WsMessage =
  | { type: "snapshot"; groups: PoolGroup[] }
  | { type: "pool_update"; group: string; pool: PoolData };
