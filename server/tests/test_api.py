import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from zircon_server.app import create_app
from zircon_server.security import decode_access_token


class ChatServerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app = create_app(
            database_path=Path(self.temp_dir.name) / "test.sqlite3",
            secret_key="test-secret-key-for-zircon-tests",
        )
        self.client = TestClient(app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def register(self, username: str) -> tuple[str, dict]:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "secure-passphrase"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        return data["access_token"], data["user"]

    def auth(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def test_registration_login_and_duplicate_username(self):
        token, user = self.register("host_user")
        self.assertEqual(user["username"], "host_user")
        self.assertEqual(
            self.client.get("/api/me", headers=self.auth(token)).json(), user
        )

        login = self.client.post(
            "/api/auth/login",
            json={"username": "HOST_USER", "password": "secure-passphrase"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["user"], user)

        duplicate = self.client.post(
            "/api/auth/register",
            json={"username": "HOST_USER", "password": "secure-passphrase"},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_host_invite_and_join_chat_server(self):
        host_token, host = self.register("host_user")
        guest_token, guest = self.register("guest_user")
        headers = self.auth(host_token)

        created = self.client.post(
            "/api/servers", headers=headers, json={"name": "Friends"}
        )
        self.assertEqual(created.status_code, 201, created.text)
        server = created.json()
        self.assertEqual(server["owner_id"], host["id"])
        self.assertTrue(server["invite_code"])

        channels = self.client.get(
            f"/api/servers/{server['id']}/channels", headers=headers
        )
        self.assertEqual([channel["name"] for channel in channels.json()], ["general"])

        joined = self.client.post(
            "/api/servers/join",
            headers=self.auth(guest_token),
            json={"invite_code": server["invite_code"]},
        )
        self.assertEqual(joined.status_code, 200)
        self.assertEqual(joined.json()["id"], server["id"])
        guest_servers = self.client.get(
            "/api/servers", headers=self.auth(guest_token)
        )
        self.assertEqual([item["id"] for item in guest_servers.json()], [server["id"]])

    def test_membership_is_required_to_read_chat(self):
        host_token, _ = self.register("host_user")
        outsider_token, _ = self.register("outsider")
        server = self.client.post(
            "/api/servers",
            headers=self.auth(host_token),
            json={"name": "Private"},
        ).json()

        response = self.client.get(
            f"/api/servers/{server['id']}/channels",
            headers=self.auth(outsider_token),
        )
        self.assertEqual(response.status_code, 404)

    def test_websocket_authenticates_and_persists_messages(self):
        host_token, _ = self.register("host_user")
        guest_token, _ = self.register("guest_user")
        server = self.client.post(
            "/api/servers",
            headers=self.auth(host_token),
            json={"name": "Chat"},
        ).json()
        self.client.post(
            "/api/servers/join",
            headers=self.auth(guest_token),
            json={"invite_code": server["invite_code"]},
        )
        channel = self.client.get(
            f"/api/servers/{server['id']}/channels",
            headers=self.auth(host_token),
        ).json()[0]
        websocket_url = (
            f"/api/servers/{server['id']}/channels/{channel['id']}/ws"
        )

        with self.client.websocket_connect(websocket_url) as websocket:
            websocket.send_json({"type": "authenticate", "token": guest_token})
            self.assertEqual(websocket.receive_json()["type"], "ready")
            websocket.send_json({"content": "Hello from a hosted server"})
            received = websocket.receive_json()
            self.assertEqual(received["message"]["content"], "Hello from a hosted server")
            self.assertEqual(received["message"]["author_username"], "guest_user")

        history = self.client.get(
            f"/api/servers/{server['id']}/channels/{channel['id']}/messages",
            headers=self.auth(host_token),
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()), 1)
        self.assertEqual(
            history.json()[0]["content"], "Hello from a hosted server"
        )

    def test_authentication_is_required_for_api(self):
        response = self.client.get("/api/servers")
        self.assertEqual(response.status_code, 401)

    def test_invalid_websocket_token_is_rejected(self):
        host_token, _ = self.register("host_user")
        server = self.client.post(
            "/api/servers",
            headers=self.auth(host_token),
            json={"name": "Chat"},
        ).json()
        channel = self.client.get(
            f"/api/servers/{server['id']}/channels",
            headers=self.auth(host_token),
        ).json()[0]
        websocket_url = (
            f"/api/servers/{server['id']}/channels/{channel['id']}/ws"
        )

        with self.assertRaises(WebSocketDisconnect) as disconnect:
            with self.client.websocket_connect(websocket_url) as websocket:
                websocket.send_json({"type": "authenticate", "token": None})
                websocket.receive_json()
        self.assertEqual(disconnect.exception.code, 4401)

    def test_malformed_access_tokens_are_rejected(self):
        for token in ("", "one.two.three", "not-a-token", None):
            with self.subTest(token=token):
                self.assertIsNone(decode_access_token(token, "test-secret"))


if __name__ == "__main__":
    unittest.main()
