import unittest
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    import androidtvremote2
except ModuleNotFoundError:
    sys.modules["androidtvremote2"] = SimpleNamespace(AndroidTVRemote=MagicMock())
try:
    import zeroconf
except ModuleNotFoundError:
    sys.modules["zeroconf"] = SimpleNamespace(ServiceBrowser=MagicMock(),
                                              ServiceListener=object, Zeroconf=MagicMock())

from tv_pairing import discover_android, scan_webos_subnets


class TVDiscoveryTest(unittest.TestCase):
    def test_android_subnet_results_are_kept_with_mdns_results(self):
        info = MagicMock()
        info.parsed_addresses.return_value = ["192.168.1.9"]
        zc = MagicMock()
        zc.get_service_info.return_value = info

        def browse(_zc, service, listener):
            listener.add_service(zc, service, "First._androidtvremote2._tcp.local.")
            return MagicMock()

        with patch("zeroconf.Zeroconf", return_value=zc), \
             patch("zeroconf.ServiceBrowser", side_effect=browse), \
             patch("tv_pairing.time.sleep"), \
             patch("tv_pairing.scan_android_subnets", return_value=[
                 {"name": "Second", "host": "192.168.1.6", "type": "android"}
             ]) as scan:
            found = discover_android(0, ["192.168.1.5"])
        self.assertEqual({item["host"] for item in found},
                         {"192.168.1.9", "192.168.1.6"})
        scan.assert_called_once_with(["192.168.1.5"])

    def test_webos_subnet_probe_finds_only_open_tv_port(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection

        def connect(address, timeout):
            if address == ("192.168.1.42", 3000):
                return connection
            raise ConnectionRefusedError

        with patch("tv_pairing.socket.create_connection", side_effect=connect):
            found = scan_webos_subnets(["192.168.1.2"])
        self.assertEqual([item["host"] for item in found], ["192.168.1.42"])


if __name__ == "__main__":
    unittest.main()
