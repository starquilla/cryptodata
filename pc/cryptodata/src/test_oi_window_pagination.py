"""Offline regression fixtures; no network, disk DB or Collector construction."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
target=Path(os.environ.get('OI_TEST_TARGET',str(Path(__file__).with_name('collect_derivatives_history.py'))))
spec=importlib.util.spec_from_file_location('oi_candidate_under_test',target)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
H=3600000;END=7*24*H

def rows(times):return [{'timestamp':t,'sumOpenInterest':'10','sumOpenInterestValue':'20','openInterest':'10'} for t in times]

class Harness:
    def __init__(self,pages,source='binance_perpetual',end=END):
        self.pages=iter(pages);self.calls=[];self.source=source
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('CREATE TABLE funding(source,symbol,time,rate,observed_at,run_id,PRIMARY KEY(source,symbol,time));CREATE TABLE oi_history(source,symbol,interval,time,value,unit,provider_field,counting_convention,provider_quote_value,observed_at,run_id,PRIMARY KEY(source,symbol,interval,time));')
        self.c=SimpleNamespace(db=self.db,end=end,id='offline-v12',coverage=[],get=self.get)
    def get(self,source,url,params):
        if 'funding' in url:return [] if source=='binance_perpetual' else {'result':{'list':[]}}
        self.calls.append(dict(params))
        try:value=next(self.pages)
        except StopIteration:raise AssertionError('Unexpected extra request')
        if isinstance(value,Exception):raise value
        return value
    def run(self):
        with patch.object(socket,'create_connection',side_effect=AssertionError('Network forbidden')):
            module.collect(self.c,self.source,'BTC','BTCUSDT')
        return self.c.coverage[-1]
    def stored(self):return [r[0] for r in self.db.execute('SELECT time FROM oi_history ORDER BY time')]
    def close(self):self.db.close()

class OIRegression(unittest.TestCase):
    def h(self,*args,**kwargs):
        h=Harness(*args,**kwargs);self.addCleanup(h.close);return h
    def test_binance_excludes_both_outer_boundaries(self):
        h=self.h([rows([-H]+list(range(0,END,H))+[END,END+H])]);r=h.run()
        self.assertEqual(h.stored(),list(range(0,END,H)));self.assertEqual(r['status'],'available')
    def test_binance_includes_start_excludes_end(self):
        h=self.h([rows(list(range(0,END,H))+[END])]);h.run();self.assertIn(0,h.stored());self.assertNotIn(END,h.stored())
    def test_bybit_excludes_both_outer_boundaries(self):
        h=self.h([{'result':{'list':rows([-H]+list(range(0,END,H))+[END]),'nextPageCursor':''}}],source='bybit_perpetual')
        self.assertEqual(h.run()['status'],'available');self.assertEqual(h.stored(),list(range(0,END,H)))
    def test_short_binance_page_not_terminal(self):
        h=self.h([rows(list(range(0,80*H,H))),rows(list(range(80*H,END,H)))]);r=h.run()
        self.assertEqual(r['status'],'available');self.assertEqual(len(h.calls),2);self.assertEqual(len(h.stored()),168)
        self.assertEqual(h.calls[1]['startTime'],80*H)
    def test_full_expected_slots_stop_without_empty_probe(self):
        h=self.h([rows(list(range(0,END,H)))]);r=h.run()
        self.assertEqual(r['status'],'available');self.assertEqual(len(h.calls),1)
    def test_short_empty_tail_retains_partial(self):
        h=self.h([rows([0,H]),[]]);r=h.run();self.assertEqual(r['status'],'partial');self.assertEqual(r['missing_hour_slots'],166)
    def test_empty_first_page_partial_not_available(self):
        h=self.h([[]]);r=h.run();self.assertEqual(r['status'],'partial');self.assertEqual(r['missing_hour_slots'],168)
    def test_missing_interior_not_hidden_by_end_coverage(self):
        h=self.h([rows([t for t in range(0,END,H) if t!=3*H]),[]]);r=h.run()
        self.assertEqual(r['status'],'partial');self.assertEqual(r['missing_hour_slots'],1)
    def test_duplicate_timestamp_not_extra_slot(self):
        h=self.h([rows([0,0,H]),[]]);r=h.run();self.assertEqual(r['rows'],2);self.assertEqual(r['missing_hour_slots'],166)
    def test_outside_timestamp_cannot_advance_past_missing_data(self):
        h=self.h([rows([0,END+10*H]),rows(list(range(H,END,H)))]);r=h.run()
        self.assertEqual(r['status'],'available');self.assertEqual(h.calls[1]['startTime'],H)
    def test_repeated_timestamp_page_keeps_partial_dataset(self):
        h=self.h([rows([0,H]),rows([0,H])]);r=h.run()
        self.assertEqual(r['status'],'partial');self.assertEqual(r['pagination_stop'],'no_progress');self.assertEqual(h.stored(),[0,H])
    def test_all_outside_page_partial_without_bad_rows(self):
        h=self.h([rows([-H,END])]);r=h.run();self.assertEqual(r['status'],'partial');self.assertEqual(r['pagination_stop'],'no_progress');self.assertEqual(h.stored(),[])
    def test_unsorted_page_progress_uses_maximum_valid_timestamp(self):
        h=self.h([rows([2*H,0,H]),rows(list(range(3*H,END,H)))]);r=h.run()
        self.assertEqual(r['status'],'available');self.assertEqual(h.calls[1]['startTime'],3*H)
    def test_shifted_window_expected_starts_at_ceil_hour(self):
        end=END+300000;h=self.h([rows([0]+list(range(H,end,H))+[end])],end=end);r=h.run()
        self.assertEqual(r['missing_hour_slots'],0);self.assertNotIn(0,h.stored());self.assertNotIn(end,h.stored())
    def test_binance_page_budget_exhaustion_is_partial(self):
        h=self.h([rows([i*H]) for i in range(30)]);r=h.run()
        self.assertEqual(len(h.calls),30);self.assertEqual(r['status'],'partial');self.assertEqual(r['missing_hour_slots'],138)
    def test_bybit_short_page_with_cursor_continues(self):
        h=self.h([{'result':{'list':rows([0]),'nextPageCursor':'a'}},{'result':{'list':rows(list(range(H,END,H))),'nextPageCursor':''}}],source='bybit_perpetual')
        self.assertEqual(h.run()['status'],'available');self.assertEqual(h.calls[1]['cursor'],'a')
    def test_bybit_empty_page_with_cursor_continues(self):
        h=self.h([{'result':{'list':[],'nextPageCursor':'a'}},{'result':{'list':rows(list(range(0,END,H))),'nextPageCursor':''}}],source='bybit_perpetual')
        self.assertEqual(h.run()['status'],'available');self.assertEqual(len(h.calls),2)
    def test_bybit_repeated_cursor_fails(self):
        h=self.h([{'result':{'list':rows([0]),'nextPageCursor':'a'}},{'result':{'list':rows([H]),'nextPageCursor':'a'}}],source='bybit_perpetual')
        r=h.run();self.assertEqual(r['status'],'partial');self.assertEqual(r['pagination_stop'],'cursor_cycle');self.assertEqual(h.stored(),[0,H])
    def test_bybit_longer_cursor_cycle_fails_before_third_request(self):
        pages=[{'result':{'list':rows([i*H]),'nextPageCursor':c}} for i,c in enumerate(['a','b','a'])]
        h=self.h(pages,source='bybit_perpetual');r=h.run();self.assertEqual(r['status'],'partial');self.assertEqual(r['pagination_stop'],'cursor_cycle');self.assertEqual(len(h.calls),3)
    def test_bybit_cursor_end_with_missing_slots_is_partial(self):
        h=self.h([{'result':{'list':rows([0]),'nextPageCursor':''}}],source='bybit_perpetual')
        self.assertEqual(h.run()['status'],'partial')
    def test_invalid_value_keeps_existing_db_unchanged(self):
        bad=rows(list(range(0,END,H)));bad[0]['sumOpenInterest']='NaN'
        h=self.h([bad]);self.assertEqual(h.run()['status'],'error');self.assertEqual(h.stored(),[])
    def test_source_exception_keeps_valid_partial_oi_dataset(self):
        h=self.h([rows([0]),RuntimeError('offline fixture failure')]);r=h.run();self.assertEqual(r['status'],'partial');self.assertEqual(r['pagination_stop'],'request_error');self.assertEqual(h.stored(),[0])
    def test_same_data_replay_does_not_duplicate(self):
        data=rows(list(range(0,END,H)));h=self.h([data,data]);h.run();h.run();self.assertEqual(len(h.stored()),168)
    def test_newest_subset_first_does_not_skip_older_missing_hours(self):
        h=self.h([rows(list(range(80*H,END,H))),rows(list(range(0,80*H,H)))]);r=h.run()
        self.assertEqual(r['status'],'available');self.assertEqual(h.calls[1]['startTime'],0);self.assertEqual(h.calls[1]['endTime'],80*H-1)
        self.assertEqual(len(h.stored()),168)
    def test_multiple_missing_groups_are_queried_in_their_own_ranges(self):
        h=self.h([rows([t for t in range(0,END,H) if t not in (H,3*H)]),rows([H]),rows([3*H])]);r=h.run()
        self.assertEqual(r['status'],'available');self.assertEqual([(x['startTime'],x['endTime']) for x in h.calls[1:]],[(H,2*H-1),(3*H,4*H-1)])
    def test_empty_missing_group_does_not_block_other_missing_groups(self):
        h=self.h([rows([t for t in range(0,END,H) if t not in (H,3*H)]),[],rows([3*H])]);r=h.run()
        self.assertEqual(r['status'],'partial');self.assertEqual(r['missing_hour_slots'],1);self.assertIn(3*H,h.stored())
    def test_failed_dataset_keeps_preexisting_rows(self):
        h=self.h([rows([0]),RuntimeError('offline failure')]);h.db.execute('INSERT INTO oi_history VALUES(?,?,?,?,?,?,?,?,?,?,?)',('binance_perpetual','BTCUSDT','1h',H,'7','base_asset','sumOpenInterest','provider_native_unverified',None,'old','old'));h.db.commit()
        self.assertEqual(h.run()['status'],'partial');self.assertEqual(h.stored(),[0,H]);self.assertEqual(h.db.execute('SELECT value FROM oi_history WHERE time=?',(H,)).fetchone()[0],'7')
    def test_request_error_before_any_page_is_partial_without_records(self):
        h=self.h([RuntimeError('offline first-page failure')]);r=h.run();self.assertEqual(r['status'],'partial');self.assertEqual(r['pagination_stop'],'request_error');self.assertEqual(h.stored(),[])
    def test_bybit_request_error_keeps_earlier_valid_rows(self):
        h=self.h([{'result':{'list':rows([0]),'nextPageCursor':'a'}},RuntimeError('offline failure')],source='bybit_perpetual')
        r=h.run();self.assertEqual(r['status'],'partial');self.assertEqual(r['pagination_stop'],'request_error');self.assertEqual(h.stored(),[0])
    def test_many_missing_groups_hit_budget_and_keep_new_records(self):
        first=rows([t for t in range(0,END,H) if (t//H)%2==0])
        h=self.h([first]+[[]]*29);r=h.run();self.assertEqual(len(h.calls),30);self.assertEqual(r['status'],'partial')
        self.assertEqual(r['pagination_stop'],'page_budget_exhausted');self.assertEqual(len(h.stored()),84)
    def test_write_failure_mid_batch_rolls_back_replaced_and_new_rows(self):
        h=self.h([rows(list(range(0,END,H)))])
        h.db.execute('INSERT INTO oi_history VALUES(?,?,?,?,?,?,?,?,?,?,?)',('binance_perpetual','BTCUSDT','1h',0,'7','base_asset','sumOpenInterest','provider_native_unverified',None,'old','old'));h.db.commit()
        h.db.execute('CREATE TRIGGER reject_second BEFORE INSERT ON oi_history WHEN NEW.time=3600000 BEGIN SELECT RAISE(ABORT,"offline write failure");END')
        r=h.run();self.assertEqual(r['status'],'error');self.assertEqual(h.stored(),[0]);self.assertEqual(h.db.execute('SELECT value FROM oi_history').fetchone()[0],'7')
    def test_invalid_later_row_rolls_back_whole_oi_dataset(self):
        bad=rows([H]);bad[0]['sumOpenInterest']='-1'
        h=self.h([rows([0]),bad,[]]);r=h.run();self.assertEqual(r['status'],'error');self.assertEqual(h.stored(),[])

if __name__=='__main__':unittest.main()
