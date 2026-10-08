from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from keyflip_api import Carrier, PromptConfig
from keyflip_api.server import ProxyConfig, make_server


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.response_body = b'{"choices":[{"message":{"content":"{\\\"score\\\":92}"}}]}'
        self.response_type = "application/json"
        self.status = 200
        parent = self

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                parent.requests.append((payload, self.headers.get("Authorization")))
                body = parent.response_body
                self.send_response(parent.status)
                self.send_header("Content-Type", parent.response_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        self.servers = []

    def tearDown(self):
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join()

    def start_proxy(self, **kwargs):
        config = ProxyConfig(
            prompt=PromptConfig(key="mock-secret", k=2, carrier_pool=tuple(
                Carrier(f"test_{i}", "Test tasks", "Test alternatives; abstain when undefined")
                for i in range(2))),
            upstream_url=f"http://127.0.0.1:{self.upstream.server_port}/v1/chat/completions",
            **kwargs,
        )
        server = make_server(config, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}/v1/chat/completions"

    def request(self, url, payload, authorization="Bearer client-token"):
        return urlopen(Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": authorization}), timeout=5)

    def test_request_injection_and_byte_exact_response(self):
        url = self.start_proxy(upstream_api_key="upstream-token", upstream_model="provider-model")
        payload = {
            "model": "client-model",
            "messages": [{"role": "user", "content": "Rate this candidate"}],
            "temperature": 0.25,
            "response_format": {"type": "json_object"},
        }
        with self.request(url, payload) as response:
            self.assertEqual(response.read(), self.response_body)
        upstream, auth = self.requests[-1]
        self.assertEqual(auth, "Bearer upstream-token")
        self.assertEqual(upstream["model"], "provider-model")
        self.assertEqual(upstream["temperature"], payload["temperature"])
        self.assertEqual(upstream["response_format"], payload["response_format"])
        self.assertIn("PRIVATE PROVIDER SCORING POLICY", upstream["messages"][0]["content"])
        self.assertNotIn("mock-secret", json.dumps(upstream))
        self.assertEqual(upstream["messages"][1:], payload["messages"])

    def test_sse_and_client_authorization_pass_through(self):
        self.response_type = "text/event-stream"
        self.response_body = b'data: {"delta":{"content":"92"}}\n\ndata: [DONE]\n\n'
        url = self.start_proxy()
        with self.request(url, {"model": "mock", "messages": [{"role": "user", "content": "Rate"}], "stream": True}) as response:
            self.assertEqual(response.headers["Content-Type"], "text/event-stream")
            self.assertEqual(response.read(), self.response_body)
        self.assertEqual(self.requests[-1][1], "Bearer client-token")
        self.assertTrue(self.requests[-1][0]["stream"])

    def test_upstream_error_does_not_echo_private_prompt(self):
        self.status = 400
        self.response_body = b'PRIVATE PROVIDER SCORING POLICY echo mock-secret'
        url = self.start_proxy()
        with self.assertRaises(HTTPError) as raised:
            self.request(url, {"messages": [{"role": "user", "content": "Rate"}]})
        with raised.exception as response:
            body = response.read()
            self.assertEqual(response.code, 400)
            self.assertNotIn(b"PRIVATE", body)
            self.assertNotIn(b"mock-secret", body)

    def test_missing_messages_do_not_reach_upstream(self):
        url = self.start_proxy()
        with self.assertRaises(HTTPError) as raised:
            self.request(url, {"model": "mock"})
        raised.exception.close()
        self.assertEqual(raised.exception.code, 400)
        self.assertFalse(self.requests)


if __name__ == "__main__":
    unittest.main()
