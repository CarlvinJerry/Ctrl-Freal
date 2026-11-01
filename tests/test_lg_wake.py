import unittest
from unittest.mock import MagicMock, patch

from lg_wake import connection_mac, neighbor_mac, wake, webos_available


class LGWakeTest(unittest.TestCase):
    def test_mac_discovery(self):
        self.assertEqual(connection_mac({"wifi": {"macAddress": "AA:BB:CC:DD:EE:FF"}}),
                         "aa:bb:cc:dd:ee:ff")
        with patch("lg_wake.subprocess.run") as run:
            run.return_value.stdout = "192.168.1.7 dev eth0 lladdr 28:11:a5:b3:4d:31 REACHABLE"
            self.assertEqual(neighbor_mac("192.168.1.7"), "28:11:a5:b3:4d:31")

    def test_wake_packet(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch("lg_wake.socket.socket", return_value=connection):
            wake("aa:bb:cc:dd:ee:ff", "192.168.1.7")
        packet = connection.sendto.call_args_list[0].args[0]
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(connection.sendto.call_count, 2)
        with self.assertRaises(ValueError):
            wake("invalid", "192.168.1.7")

    def test_webos_probe(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch("lg_wake.socket.create_connection", return_value=connection) as probe:
            self.assertTrue(webos_available("192.168.1.7"))
            probe.assert_called_once_with(("192.168.1.7", 3000), timeout=0.7)
        with patch("lg_wake.socket.create_connection", side_effect=ConnectionRefusedError):
            self.assertFalse(webos_available("192.168.1.7"))


if __name__ == "__main__":
    unittest.main()
