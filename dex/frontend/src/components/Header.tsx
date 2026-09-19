interface HeaderProps {
  connected: boolean;
  poolCount: number;
  lastUpdate: number;
}

export function Header({ connected, poolCount, lastUpdate }: HeaderProps) {
  const statusText = connected ? "Connected" : "Disconnected";
  const timeStr = lastUpdate
    ? new Date(lastUpdate).toLocaleTimeString()
    : "--";

  return (
    <div className="header">
      <h1 className="header__title">Arb Bot Dashboard</h1>
      <div className="header__stats">
        <div className="header__stat">
          <span
            className={`header__dot ${connected ? "header__dot--connected" : "header__dot--disconnected"}`}
          />
          <span className="header__status-text">{statusText}</span>
        </div>
        <div className="header__stat">
          <span className="header__label">Pools</span>
          <span className="header__value">{poolCount}</span>
        </div>
        <div className="header__stat">
          <span className="header__label">Last Update</span>
          <span className="header__value">{timeStr}</span>
        </div>
      </div>
    </div>
  );
}
