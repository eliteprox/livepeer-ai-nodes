"""No-network lifecycle tests for the paid frame adapter."""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

package = types.ModuleType("creative_test")
package.__path__ = [str(Path(__file__).parents[1] / "nodes")]
sys.modules[package.__name__] = package
remote = importlib.import_module("creative_test.creative_remote")


class LifecycleTests(unittest.TestCase):
    def test_node_executes_inside_comfy_running_loop(self):
        import numpy as np
        import torch
        async def check():
            with patch.object(remote, "_account", return_value=("https://signer.example", {})), \
                 patch.object(remote, "_frames", AsyncMock(return_value=(
                     [np.zeros((8, 8, 3), dtype=np.uint8)], {"frames_received": 1}))):
                image, report = await remote.LivepeerRemoteFrames().run(
                    torch.zeros((1, 8, 8, 3)), "comfystream/sd-turbo", "batch", "test", 1, 8, 5, True)
                self.assertEqual(tuple(image.shape), (1, 8, 8, 3))
            with patch.object(remote, "stream_signer") as signer:
                with self.assertRaisesRegex(RuntimeError, "Enable paid execution"):
                    await remote.LivepeerFalImageBatch().run("test", 1, 42, False)
                signer.assert_not_called()
        asyncio.run(check())

    def test_execution_disabled_before_credentials_or_network(self):
        with patch.object(remote, "stream_signer") as signer:
            with self.assertRaisesRegex(RuntimeError, "Enable paid execution"):
                remote._account(False)
            signer.assert_not_called()

    def test_failure_stops_funding_and_releases_session(self):
        async def check():
            events = []
            lease = types.SimpleNamespace(app_url="https://runner.example/session/app",
                stop_payments=AsyncMock(side_effect=lambda: events.append("funding-stopped")))
            async def release(value):
                self.assertIs(value, lease)
                events.append("released")
            with patch.object(remote, "_discover", AsyncMock(return_value={})), \
                 patch.object(remote, "_reserve_pixels", AsyncMock(return_value=lease)), \
                 patch.object(remote, "_json", AsyncMock(side_effect=RuntimeError("startup failed"))), \
                 patch.object(remote, "stop_runner_session", release):
                with self.assertRaisesRegex(RuntimeError, "startup failed"):
                    await remote._frames([], "comfystream/sd-turbo", "test", "batch", 1, 8, 5,
                                         "https://signer.example", {})
            self.assertEqual(events, ["funding-stopped", "released"])
        asyncio.run(check())

    def test_graph_links_and_paid_defaults(self):
        import json
        for mode in ("live", "batch"):
            graph = json.loads((Path(__file__).parents[1] / "workflows" /
                                f"botanical-signal-{mode}.json").read_text())
            nodes = {node["id"]: node for node in graph["nodes"]}
            for node in nodes.values():
                if node["type"].startswith("Livepeer"):
                    self.assertIs(node["widgets_values"][-1], False)
            for link_id, source, output, target, input_, type_ in graph["links"]:
                self.assertIn(link_id, nodes[source]["outputs"][output]["links"])
                self.assertEqual(nodes[target]["inputs"][input_]["link"], link_id)


if __name__ == "__main__":
    unittest.main()
