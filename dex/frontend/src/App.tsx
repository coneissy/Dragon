import { Header } from "./components/Header";
import { PoolTable } from "./components/PoolTable";
import { usePoolData } from "./hooks/usePoolData";

export function App() {
  const { groups, connected, lastUpdate, poolCount } = usePoolData();

  return (
    <div className="app">
      <Header
        connected={connected}
        poolCount={poolCount}
        lastUpdate={lastUpdate}
      />
      {poolCount === 0 ? (
        <div className="app__empty">
          {connected
            ? "No pools loaded yet..."
            : "Connecting to bot WS server on :3001..."}
        </div>
      ) : (
        <PoolTable groups={groups} />
      )}
    </div>
  );
}
