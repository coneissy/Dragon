import { useEffect, useMemo, useState } from "react";
import type { PoolData, PoolGroup } from "../types";
import { PriceCell } from "./PriceCell";

interface PoolTableProps {
  groups: PoolGroup[];
}

interface PairGroup {
  pair: string;
  pools: PoolData[];
  spread?: number;
}

function timeAgo(ts: number): { text: string; stale: boolean } {
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 5) return { text: "just now", stale: false };
  if (diff < 60) return { text: `${diff}s ago`, stale: false };
  return { text: `${Math.floor(diff / 60)}m ago`, stale: true };
}

function badgeClass(poolType: string): string {
  const lower = poolType.toLowerCase();
  if (lower.includes("v2")) return "badge badge--v2";
  if (lower.includes("v3")) return "badge badge--v3";
  return "badge badge--default";
}

function formatTvl(tvl?: number): string {
  if (tvl == null) return "\u2014";
  if (tvl >= 1_000_000) return `$${(tvl / 1_000_000).toFixed(2)}M`;
  if (tvl >= 1_000) return `$${(tvl / 1_000).toFixed(1)}K`;
  return `$${tvl.toFixed(0)}`;
}

function spreadClass(spread: number): string {
  if (spread > 0.5) return "spread--hot";
  if (spread >= 0.1) return "spread--warm";
  return "spread--cold";
}

export function PoolTable({ groups }: PoolTableProps) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const pairGroups = useMemo(() => {
    return groups.map((group): PairGroup => {
      const prices = group.pools
        .map((p) => p.price0)
        .filter((p): p is number => p != null && p > 0);

      let spread: number | undefined;
      if (prices.length >= 2) {
        const min = Math.min(...prices);
        const max = Math.max(...prices);
        spread = ((max - min) / min) * 100;
      }

      return { pair: group.name, pools: group.pools, spread };
    });
  }, [groups]);

  return (
    <div className="pool-table">
      <table className="pool-table__table">
        <thead>
          <tr>
            <th className="pool-table__th">Pair</th>
            <th className="pool-table__th">DEX</th>
            <th className="pool-table__th">Fee</th>
            <th className="pool-table__th pool-table__th--right">Price</th>
            <th className="pool-table__th pool-table__th--right">TVL</th>
            <th className="pool-table__th pool-table__th--right">Spread</th>
            <th className="pool-table__th">Updated</th>
            <th className="pool-table__th">Link</th>
          </tr>
        </thead>
        <tbody>
          {pairGroups.map((group) =>
            group.pools.map((pool, i) => {
              const { text: timeText, stale } = timeAgo(pool.lastUpdate);
              const isGroupStart = i === 0;

              return (
                <tr
                  key={pool.address}
                  className={`pool-table__row${isGroupStart ? " pool-table__row--group-start" : ""}`}
                >
                  <td className="pool-table__cell">
                    {isGroupStart ? (
                      <span className="pool-table__pair">{group.pair}</span>
                    ) : (
                      <span className="pool-table__sub-arrow">&darr;</span>
                    )}
                  </td>
                  <td className="pool-table__cell">
                    <span className={badgeClass(pool.poolType)}>
                      {pool.label ?? pool.poolType}
                    </span>
                  </td>
                  <td className="pool-table__cell pool-table__cell--fee">
                    {(pool.fee * 100).toFixed(2)}%
                  </td>
                  <PriceCell value={pool.price0} />
                  <td className="pool-table__cell pool-table__cell--right">
                    {formatTvl(pool.tvlQuote)}
                  </td>
                  <td className="pool-table__cell pool-table__cell--right">
                    {isGroupStart && group.spread != null ? (
                      <span className={spreadClass(group.spread)}>
                        {group.spread.toFixed(3)}%
                      </span>
                    ) : null}
                  </td>
                  <td className="pool-table__cell">
                    <span className={`time-ago${stale ? " time-ago--stale" : ""}`}>
                      {timeText}
                    </span>
                  </td>
                  <td className="pool-table__cell">
                    <a
                      href={`https://basescan.org/address/${pool.address}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="pool-link"
                    >
                      basescan
                    </a>
                  </td>
                </tr>
              );
            }),
          )}
        </tbody>
      </table>
    </div>
  );
}
