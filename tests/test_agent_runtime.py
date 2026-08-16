import tempfile
import unittest
from pathlib import Path

from autodraw.renderer_adapter import RendererError, renderer_source_manifest
from autodraw.runtime import runtime_identity
from autodraw.spec import build_agent_spec, spec_sha256


class AgentRuntimeTests(unittest.TestCase):
    def test_runtime_identity_uses_the_authoritative_semantic_spec_hash(self):
        identity = runtime_identity()
        self.assertEqual(identity["agent_spec_sha256"], spec_sha256(build_agent_spec()))
        self.assertEqual(identity["runtime_mode"], "source")

    def test_alternate_renderer_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RendererError, "内置绘图引擎"):
                renderer_source_manifest(Path(directory))


if __name__ == "__main__":
    unittest.main()
