import unittest
from probe_cloud_sources import validate
class ProbeTests(unittest.TestCase):
    def test_http200_provider_error_is_failure(self):
        with self.assertRaises(ValueError):validate('bybit_funding',{'retCode':10001,'result':{'list':[]}})
    def test_empty_settlement_not_pass(self):
        with self.assertRaises(IndexError):validate('binance_funding',[])
    def test_nonfinite_rate_rejected(self):
        with self.assertRaises(ValueError):validate('binance_funding',[{'fundingRate':'NaN'}])
    def test_zero_settled_rate_valid(self):self.assertEqual(validate('binance_funding',[{'fundingRate':'0'}]),1)
if __name__=='__main__':unittest.main()
