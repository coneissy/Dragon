import type { IPoolMath, PoolConfig, V2PoolState, V3PoolState } from "@/pools/types";
import { PoolType } from "@/pools/types";
import { V2Pool } from "@/pools/types/uniswap-v2";
import { V3Pool } from "@/pools/types/uniswap-v3";
import { PancakeV3Pool } from "@/pools/types/pancakeswap-v3";
import { AerodromeV2Pool } from "@/pools/types/aerodrome-v2";

export function createPool(
  config: PoolConfig,
  state?: V2PoolState | V3PoolState,
): IPoolMath {
  switch (config.poolType) {
    case PoolType.UniswapV2:
      return new V2Pool(config, (state as V2PoolState) ?? {});

    case PoolType.AerodromeV2:
      return new AerodromeV2Pool(config, (state as V2PoolState) ?? {});

    case PoolType.UniswapV3:
      return new V3Pool(config, (state as V3PoolState) ?? {});

    case PoolType.PancakeV3:
      return new PancakeV3Pool(config, (state as V3PoolState) ?? {});

    default:
      throw new Error(`Unknown pool type: ${config.poolType}`);
  }
}
