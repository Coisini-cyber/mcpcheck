import json
import os
import sys
import tempfile
import unittest

from mcpcheck.client import (
    ProtocolError,
    StdioTransport,
    TransportError,
    make_transport,
    parse_message,
    parse_response,
)
from mcpcheck.demo import write_demo


class ParseTest(unittest.TestCase):
    def test_valid_result(self):
        response = parse_response('{"jsonrpc":"2.0","id":1,"result":{}}', 1)
        self.assertEqual(response.id, 1)
        self.assertFalse(response.is_error)
        self.assertEqual(response.result, {})

    def test_valid_error(self):
        response = parse_response(
            '{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"nope"}}', 1
        )
        self.assertTrue(response.is_error)
        self.assertEqual(response.error_code, -32601)

    def test_missing_jsonrpc(self):
        with self.assertRaises(ProtocolError):
            parse_response('{"id":1,"result":{}}', 1)

    def test_wrong_jsonrpc(self):
        with self.assertRaises(ProtocolError):
            parse_response('{"jsonrpc":"1.0","id":1,"result":{}}', 1)

    def test_both_result_and_error(self):
        with self.assertRaises(ProtocolError):
            parse_response('{"jsonrpc":"2.0","id":1,"result":{},"error":{}}', 1)

    def test_neither_result_nor_error(self):
        with self.assertRaises(ProtocolError):
            parse_response('{"jsonrpc":"2.0","id":1}', 1)

    def test_result_must_be_an_object(self):
        with self.assertRaises(ProtocolError):
            parse_response('{"jsonrpc":"2.0","id":1,"result":[]}', 1)

    def test_error_must_be_an_object(self):
        with self.assertRaises(ProtocolError):
            parse_message({"jsonrpc": "2.0", "id": 1, "error": "boom"}, 1)

    def test_invalid_json(self):
        with self.assertRaises(ProtocolError):
            parse_response("not json", 1)

    def test_empty_line(self):
        with self.assertRaises(ProtocolError):
            parse_response("   ", 1)

    def test_non_object(self):
        with self.assertRaises(ProtocolError):
            parse_response("[1,2,3]", 1)


class TransportFactoryTest(unittest.TestCase):
    def test_requires_a_target(self):
        with self.assertRaises(TransportError):
            make_transport()

    def test_rejects_both(self):
        with self.assertRaises(TransportError):
            make_transport(command="python x.py", url="http://localhost")

    def test_stdio(self):
        transport = make_transport(command="python x.py")
        self.assertIsInstance(transport, StdioTransport)


class StdioTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.paths = write_demo(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def command(self, name="compliant"):
        return '"%s" "%s"' % (sys.executable, self.paths[name])

    def test_round_trip(self):
        transport = StdioTransport(self.command(), timeout=10)
        try:
            transport.start()
            response = transport.request("ping")
            self.assertFalse(response.is_error)
            self.assertEqual(response.id, transport.last_request_id)
        finally:
            transport.close()

    def test_raw_write_and_read(self):
        transport = StdioTransport(self.command(), timeout=10)
        try:
            transport.start()
            transport.write_raw('{"jsonrpc":"2.0","id":99,"method":"ping"}')
            line = transport.read_raw()
            self.assertIn('"id": 99', line)
        finally:
            transport.close()

    def test_stderr_is_captured(self):
        transport = StdioTransport(self.command(), timeout=10)
        try:
            transport.start()
            transport.request("ping")
            self.assertIsInstance(transport.stderr_tail, str)
        finally:
            transport.close()

    def test_dead_process_raises(self):
        transport = StdioTransport('"%s" -c "import sys; sys.exit(0)"' % sys.executable, timeout=5)
        transport.start()
        try:
            with self.assertRaises(TransportError):
                transport.read_raw(timeout=2)
        finally:
            transport.close()

    def test_missing_command(self):
        transport = StdioTransport("definitely-not-a-real-command-xyz", timeout=2)
        with self.assertRaises(TransportError):
            transport.start()


if __name__ == "__main__":
    unittest.main()
