import unittest
from build_dashboard import metrics,oi_change
class MetricTests(unittest.TestCase):
    def test_oi_counting_convention_change_not_a_market_move(self):
        current={'value':'100','provider_field':'singleOpenInterest','counting_convention':'single_side','unit':'base_asset'}
        previous={'value':'200','provider_field':'openInterest','counting_convention':'both_sides','unit':'base_asset'}
        self.assertIsNone(oi_change(current,previous));self.assertAlmostEqual(oi_change(current,current),0)
    def test_new_listing_short_history(self):
        x=metrics([(86400000*i,str(100+i)) for i in range(5)]);self.assertIsNone(x['sma_20']);self.assertIsNone(x['return_180d_pct']);self.assertIsNone(x['max_drawdown_observed_90d_pct'])
    def test_gap_disables_averages(self):
        x=metrics([(86400000*i,str(100+i)) for i in range(60) if i!=25]);self.assertFalse(x['contiguous_daily_history']);self.assertIsNone(x['sma_50'])
    def test_returns_use_calendar_days(self):
        x=metrics([(86400000*i,str(100+i)) for i in range(201)]);self.assertAlmostEqual(x['return_180d_pct'],(300/120-1)*100);self.assertEqual(x['trend'],'above_20_50')
if __name__=='__main__':unittest.main()
