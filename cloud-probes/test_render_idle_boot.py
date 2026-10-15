import os
import unittest
from unittest.mock import patch
import render_probe_server as server


class IdleBootTests(unittest.TestCase):
    def test_default_boot_does_not_start_probe_even_with_legacy_environment(self):
        with patch.dict(os.environ, {'PROBE_MODE':'coverage','PROBE_ROUNDS':'3'}), \
             patch.object(server.threading,'Thread') as thread, \
             patch.object(server,'ThreadingHTTPServer') as http, \
             patch.object(server.subprocess,'Popen') as process:
            server.main([])
            thread.assert_not_called()
            process.assert_not_called()
            http.return_value.serve_forever.assert_called_once()
            self.assertEqual(server.STATE['status'],'IDLE')
            self.assertIs(server.STATE['provider_requests_enabled'],False)

    def test_explicit_sample_overrides_legacy_coverage_mode(self):
        with patch.dict(os.environ, {'PROBE_MODE':'coverage'}), \
             patch.object(server.threading,'Thread') as thread, \
             patch.object(server,'ThreadingHTTPServer'):
            server.main(['--run-once'])
            thread.return_value.start.assert_called_once()
            self.assertEqual(os.environ['PROBE_MODE'],'sample')
            self.assertEqual(os.environ['PROBE_ROUNDS'],'1')

    def test_coverage_rounds_must_be_explicit(self):
        with patch.dict(os.environ, {}), \
             patch.object(server.threading,'Thread'), \
             patch.object(server,'ThreadingHTTPServer'):
            server.main(['--run-once','--mode','coverage','--rounds','3'])
            self.assertEqual(os.environ['PROBE_MODE'],'coverage')
            self.assertEqual(os.environ['PROBE_ROUNDS'],'3')

    def test_invalid_arguments_fail_before_probe_or_listener(self):
        with patch.object(server.threading,'Thread') as thread, \
             patch.object(server,'ThreadingHTTPServer') as http:
            with self.assertRaises(SystemExit):server.main(['--rounds','0'])
            thread.assert_not_called()
            http.assert_not_called()


if __name__=='__main__':unittest.main()
