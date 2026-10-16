import csv, datetime as dt, json, pathlib, sqlite3
R=pathlib.Path(r'E:\CryptoData tai nguyen')
reports=sorted(R.joinpath('reports').glob('*-pilot.json'))
if not reports:raise SystemExit('No completed pilot report')
r=json.loads(reports[-1].read_text(encoding='utf8'))
db=sqlite3.connect(R/'normalized/market.sqlite3')
checks={'sqlite_integrity':db.execute('PRAGMA integrity_check').fetchone()[0]}
for table in ['candles','funding','oi']:
    n=db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
    db.execute('SAVEPOINT replay_check')
    db.execute('INSERT OR IGNORE INTO '+table+' SELECT * FROM '+table)
    checks[table+'_duplicate_replay_stable']=db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==n
    db.execute('ROLLBACK TO replay_check');db.execute('RELEASE replay_check')
checks['future_or_open_candles']=db.execute('SELECT COUNT(*) FROM candles WHERE open_time>=?',(r['window_end_ms'],)).fetchone()[0]
configured=set(json.loads((R/'config/settings.json').read_text(encoding='utf-8-sig'))['assets'])
checks['unconfigured_assets']=sorted({x[0] for x in db.execute('SELECT DISTINCT base FROM candles')} - configured)
db.close()
fields=['source','asset','symbol','dataset','status','rows','expected','missing','identity','error','scope','possibly_truncated']
with (R/'reports'/'market-data-coverage.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(r['coverage'])
(R/'reports'/'quality-checks.json').write_text(json.dumps(checks,indent=2),encoding='utf8')
lines=['# Lô dữ liệu thử nghiệm đầu tiên','', 'Thời điểm báo cáo: '+dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).isoformat(),'',
       'Run: '+r['run_id'],'Thời gian chạy: '+str(r['seconds'])+' giây',
       'Số lần gọi kể cả thử lại: '+str(r['request_attempts']),
       'Dữ liệu gốc nén: '+str(r['compressed_raw_bytes'])+' byte',
       'SQLite: '+str(r['database_bytes'])+' byte',
       'Số bản ghi: '+json.dumps(r['database_rows']),
       'Lỗi được ghi nhận: '+str(r['errors']),'',
       '## Độ phủ nến', '']
for src in ['binance_spot','okx_spot']:
    x=[v for v in r['coverage'] if v.get('source')==src and v.get('dataset')=='candles_5m']
    lines.append(src+': '+str(len(x))+' cặp; '+str(sum(v['status']=='complete' for v in x))+' đủ cửa sổ; '+str(sum(v['status']=='partial' for v in x))+' thiếu kỳ.')
lines+=['','## Giới hạn','',
'Khớp ticker/base/quote chưa phải xác minh contract. Không dùng kết quả này tự động đặt lệnh.',
'OI chỉ là mẫu gần nhất, không phải lịch sử. Funding chưa annualize. DefiLlama chỉ lưu snapshot gốc.',
'Bản thử chưa có lịch chạy, sao lưu tự động hoặc bảo đảm chịu tải sáu tháng. Không ngoại suy dung lượng lần tải lịch sử thành dung lượng mỗi hai giờ.',
'Chi tiết: market-data-coverage.csv, quality-checks.json và báo cáo JSON cùng run.']
(R/'reports'/'pilot-summary.md').write_text('\n'.join(lines),encoding='utf8')
print(json.dumps({'summary':str(R/'reports'/'pilot-summary.md'),'checks':checks,'rows':r['database_rows'],'errors':[v for v in r['coverage'] if v['status']=='error'],'missing_pairs':[v for v in r['coverage'] if v['status']=='no_eligible_pair'],'seconds':r['seconds'],'raw_bytes':r['compressed_raw_bytes'],'db_bytes':r['database_bytes'],'requests':r['request_attempts']},indent=2))
