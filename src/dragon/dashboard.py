"""Single-source live dashboard loader for the Dragon arbitrage engine.

The canonical dashboard lives at the repository root. This loader adds a
prominent live Binance account/balance panel without duplicating the dashboard.
"""
from pathlib import Path


_DASHBOARD_PATH = Path(__file__).resolve().parents[2] / "dashboard.html"

if not _DASHBOARD_PATH.is_file():
    raise RuntimeError(f"Dragon dashboard file not found: {_DASHBOARD_PATH}")

_HTML = _DASHBOARD_PATH.read_text(encoding="utf-8")

_BALANCE_PANEL = r'''
<section class="card hero" id="binanceBalancePanel" style="margin-top:14px">
  <div class="top">
    <div>
      <div class="label">Binance account</div>
      <div style="font-size:20px;font-weight:850">💰 USDT Balance</div>
    </div>
    <div id="binanceAuth" class="pill">Checking account...</div>
  </div>
  <div class="grid" style="margin:12px 0 0">
    <div class="card"><div class="label">Available USDT</div><div id="binanceFreeUsdt" class="value big">--</div></div>
    <div class="card"><div class="label">Balance refreshes</div><div id="binanceBalanceRefreshes" class="value">--</div></div>
    <div class="card"><div class="label">Account data</div><div id="binanceBalanceStatus" class="value">--</div></div>
  </div>
</section>
<script>
(function(){
  const q=id=>document.getElementById(id);
  async function balanceTick(){
    try{
      const r=await fetch('/health?balance_ts='+Date.now(),{cache:'no-store'});
      const j=await r.json(); const s=j.state||j||{};
      const auth=!!s.binance_authenticated;
      const raw=s.free_usdt;
      const num=Number(raw);
      q('binanceFreeUsdt').textContent=Number.isFinite(num)?num.toLocaleString(undefined,{minimumFractionDigits:6,maximumFractionDigits:6})+' USDT':String(raw??'--');
      q('binanceAuth').innerHTML='<span class="'+(auth?'good':'bad')+'">● '+(auth?'BINANCE API AUTHENTICATED':'BINANCE API NOT AUTHENTICATED')+'</span>';
      const refresh=Number(s.balance_refreshes||0);
      q('binanceBalanceRefreshes').textContent=refresh.toLocaleString();
      q('binanceBalanceStatus').textContent=auth&&refresh>0?'LIVE':'WAITING';
      q('binanceBalanceStatus').className='value '+(auth&&refresh>0?'good':'warn');
    }catch(e){
      q('binanceBalanceStatus').textContent='ERROR';
      q('binanceBalanceStatus').className='value bad';
    }
  }
  balanceTick(); setInterval(balanceTick,2000);
})();
</script>
'''

# Insert immediately below the warning area. If the marker changes, fail loudly
# rather than silently serving a dashboard without the balance panel.
_MARKER = '<div id="warnings"></div>'
if _MARKER not in _HTML:
    raise RuntimeError("Dragon dashboard warnings marker not found")

HTML = _HTML.replace(_MARKER, _MARKER + _BALANCE_PANEL, 1)
