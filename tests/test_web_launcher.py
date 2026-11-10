"""Launcher routing is checked with mocks, without accessing a browser or socket."""
import io
import json
import sys
import unittest
from unittest.mock import patch

import launch_web


class WebLauncherTests(unittest.TestCase):
    def test_reuses_only_the_verified_application(self):
        for payload,expected in (({'application_id':'after-close-lab'},True),({'application_id':'other-app'},False),([],False)):
            with patch('launch_web.build_opener') as opener:
                opener.return_value.open.return_value=io.BytesIO(json.dumps(payload).encode())
                self.assertEqual(launch_web.running_instance('http://127.0.0.1:8766'),expected)

    def test_existing_server_does_not_start_a_second_copy(self):
        with (patch.object(sys,'argv',['launch_web.py']),patch('launch_web.running_instance',return_value=True),
              patch('launch_web.webbrowser.open') as browser,patch('app.main') as serve):
            launch_web.main()
        browser.assert_called_once_with('http://127.0.0.1:8766');serve.assert_not_called()

    def test_missing_server_starts_web_entry_with_explicit_port(self):
        with (patch.object(sys,'argv',['launch_web.py','--port','8766','--no-open']),
              patch('launch_web.running_instance',return_value=False),patch('app.main') as serve):
            launch_web.main()
            self.assertEqual(sys.argv,['app.py','--port','8766'])
            serve.assert_called_once()


if __name__=='__main__':unittest.main()
