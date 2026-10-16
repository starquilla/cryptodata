"""Synthetic offline fixtures; no production files or provider calls."""
NOW_MS = 1792152000000
SCHEMAS = {
 'candles': 'CREATE TABLE candles(source TEXT,market TEXT,symbol TEXT,base TEXT,quote TEXT,interval TEXT,open_time INTEGER,open TEXT,high TEXT,low TEXT,close TEXT,base_volume TEXT,observed_at TEXT,run_id TEXT,PRIMARY KEY(source,market,symbol,interval,open_time))',
 'funding': 'CREATE TABLE funding(source TEXT,symbol TEXT,time INTEGER,rate TEXT,observed_at TEXT,run_id TEXT,PRIMARY KEY(source,symbol,time))',
 'oi_history': 'CREATE TABLE oi_history(source TEXT,symbol TEXT,interval TEXT,time INTEGER,value TEXT,unit TEXT,provider_field TEXT,counting_convention TEXT,provider_quote_value TEXT,observed_at TEXT,run_id TEXT,PRIMARY KEY(source,symbol,interval,time))'
}
def sample_rows():
    provenance=['2026-10-16T12:00:00+00:00','synthetic-offline-fixture']
    return {
      'candles': [['binance_spot','spot','BTCUSDT','BTC','USDT','5m',NOW_MS-(4-i)*300000,'100','110','90','105','10',*provenance] for i in range(3)],
      'funding': [['binance_perpetual','BTCUSDT',NOW_MS-(4-i)*3600000,'0.0001',*provenance] for i in range(3)],
      'oi_history': [['binance_perpetual','BTCUSDT','1h',NOW_MS-(4-i)*3600000,'10','base_asset','sumOpenInterest','provider_native_unverified',None,*provenance] for i in range(3)]
    }
