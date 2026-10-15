import json
import os
import unittest
from unittest.mock import patch
from probe_cloud_sources import configured_sources, PROBES
from probe_asset_coverage import jobs


class SourceRoutingTests(unittest.TestCase):
    def test_unknown_empty_duplicate_and_wrong_type_fail_closed(self):
        for value in ([],['unknown'],['okx_spot','okx_spot'],{},'okx_spot',[1]):
            with self.subTest(value=value),self.assertRaises(ValueError):
                configured_sources({'PROBE_SOURCES_JSON':json.dumps(value)})

    def test_unset_keeps_existing_all_source_semantics(self):
        self.assertEqual(configured_sources({}),{x[0] for x in PROBES})

    def test_healthy_route_excludes_blocked_hosts(self):
        names={x[0] for x in PROBES}-{'binance_perpetual','hyperliquid_perpetual'}
        with patch.dict(os.environ,{'PROBE_SOURCES_JSON':json.dumps(sorted(names))}), \
             patch('probe_asset_coverage.pathlib.Path.read_text',side_effect=[
                 json.dumps({'binance_spot|BTC':'BTCUSDT','binance_perpetual|BTC':'BTCUSDT','bybit_perpetual|BTC':'BTCUSDT'}),
                 json.dumps({'hyperliquid_perpetual|BTC':{'symbol':'BTC'}})]):
            tasks,_=jobs()
        self.assertTrue(tasks)
        self.assertFalse(any('fapi.binance.com' in t[4] or 'hyperliquid' in t[4] for t in tasks))
        self.assertTrue(any(t[0]=='bybit_perpetual' for t in tasks))

    def test_single_source_route_does_not_mix_shared_snapshots(self):
        with patch.dict(os.environ,{'PROBE_SOURCES_JSON':'["binance_perpetual"]'}), \
             patch('probe_asset_coverage.pathlib.Path.read_text',side_effect=[
                 json.dumps({'binance_perpetual|BTC':'BTCUSDT','okx_spot|BTC':'BTC-USDT'}),'{}']):
            tasks,_=jobs()
        self.assertEqual(len(tasks),3)
        self.assertEqual({t[0] for t in tasks},{'binance_perpetual'})


if __name__=='__main__':unittest.main()
