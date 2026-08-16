from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autodraw.agent_tasks import (
    create_agent_task,
    record_human_visual_review,
    submit_agent_request,
    validate_agent_request,
)
from autodraw.renderer_adapter import DEFAULT_RENDERER_ROOT
from autodraw.zosapi_provider import _sha256
from tests.test_autodraw_mapper import _virtual_surface_triplet


class FakeProvider:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def extract(self, source):
        path = Path(source)
        system = _virtual_surface_triplet()
        system.source_file = str(path.resolve())
        system.source_sha256 = _sha256(path)
        system.source_size = path.stat().st_size
        system.title = "Test2"
        return system


class AgentTaskContractTests(unittest.TestCase):
    def make_task(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "input.zmx"
        source.write_text("synthetic zmx", encoding="ascii")
        task = root / "task"
        with patch("autodraw.agent_tasks.NativeZosApiProvider", FakeProvider):
            create_agent_task(source, task, renderer_root=DEFAULT_RENDERER_ROOT)
        request = json.loads((task / "agent_request.json").read_text(encoding="utf-8"))
        return temporary, task, request

    @staticmethod
    def complete_request(request):
        request = copy.deepcopy(request)
        request["user_evidence"] = [
            {
                "id": "op",
                "kind": "operator_record",
                "content": "test mode confirmation",
                "captured_at": "2026-08-13T10:00:00+08:00",
                "source_ref": "unit-test",
            }
        ]
        request["requirement_analysis"] = {
            "user_goal_summary": "Exercise the Agent request contract.",
            "decisions": [
                {
                    "category": "naming",
                    "statement": "Use generated names.",
                    "evidence_ids": ["op"],
                },
                {
                    "category": "geometry_review",
                    "statement": "Confirm MD2 without changing it.",
                    "evidence_ids": ["op"],
                },
                {
                    "category": "manufacturing_complete",
                    "statement": "Use the full renderer defaults.",
                    "evidence_ids": ["op"],
                },
            ],
            "evidence_disposition": {
                "op": {
                    "status": "mapped",
                    "targets": [
                        "naming",
                        "geometry_review.1.MD2",
                        "manufacturing.defaults",
                    ],
                    "explanation": "One test record covers the fixture decisions.",
                }
            },
            "assumptions": [],
            "unresolved_questions": [],
        }
        request["naming"] = {
            "mode": "generated",
            "confirm_generated_names": True,
            "evidence_ids": ["op"],
        }
        request["geometry_review"].update(
            {
                "approval_status": "approved",
                "approved_by": "unit-test",
                "approved_at": "2026-08-13T10:00:00+08:00",
                "reason": "exact acknowledgement",
                "evidence_ids": ["op"],
            }
        )
        request["manufacturing_requirements"].update(
            {
                "approval_status": "approved",
                "approve_effective_manufacturing_requirements": True,
                "approved_by": "unit-test",
                "approved_at": "2026-08-13T10:00:00+08:00",
                "reason": "full defaults accepted",
                "evidence_ids": ["op"],
            }
        )
        request["execution"]["mode"] = "test"
        return request

    @staticmethod
    def submit(task, request):
        candidate = task.parent / "candidate.json"
        candidate.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        submit_agent_request(task, candidate)

    def test_complete_test_request_validates(self):
        temporary, task, request = self.make_task()
        with temporary:
            self.assertTrue(request["execution"]["human_visual_review_required"])
            self.assertNotIn("agent_visual_review_required", request["execution"])
            self.submit(task, self.complete_request(request))
            validation = validate_agent_request(task)
        self.assertTrue(validation["valid"], validation["errors"])

    def test_unversioned_request_edit_is_rejected(self):
        temporary, task, request = self.make_task()
        with temporary:
            self.submit(task, self.complete_request(request))
            active = json.loads((task / "agent_request.json").read_text(encoding="utf-8"))
            active["requirement_analysis"]["user_goal_summary"] += " tampered"
            (task / "agent_request.json").write_text(
                json.dumps(active, ensure_ascii=False), encoding="utf-8"
            )
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("未版本化修改" in error for error in validation["errors"]))

    def test_protocol_snapshot_tamper_is_rejected(self):
        temporary, task, request = self.make_task()
        with temporary:
            self.submit(task, self.complete_request(request))
            protocol = task / "AGENT_PROTOCOL.md"
            protocol.write_text(
                protocol.read_text(encoding="utf-8") + "\ntampered\n",
                encoding="utf-8",
            )
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(
            any("AGENT_PROTOCOL.md 已被修改" in error for error in validation["errors"])
        )

    def test_spec_snapshot_tamper_is_rejected(self):
        temporary, task, request = self.make_task()
        with temporary:
            self.submit(task, self.complete_request(request))
            spec = task / "lens_drawing_agent_spec.json"
            spec.write_text(spec.read_text(encoding="utf-8") + " ", encoding="utf-8")
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("Agent spec 已被修改" in error for error in validation["errors"]))

    def test_attachment_hash_mismatch_is_rejected(self):
        temporary, task, request = self.make_task()
        with temporary:
            attachment = task.parent / "requirements.txt"
            attachment.write_text("original", encoding="utf-8")
            candidate = self.complete_request(request)
            candidate["user_evidence"].append(
                {
                    "id": "attachment",
                    "kind": "attachment",
                    "content": "manufacturing requirements attachment",
                    "source_ref": str(attachment),
                    "sha256": _sha256(attachment),
                }
            )
            candidate["requirement_analysis"]["evidence_disposition"]["attachment"] = {
                "status": "no_action",
                "targets": [],
                "explanation": "Hash integrity test only.",
            }
            attachment.write_text("replaced", encoding="utf-8")
            self.submit(task, candidate)
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("当前哈希" in error for error in validation["errors"]))

    def test_source_analysis_tamper_is_rejected(self):
        temporary, task, request = self.make_task()
        with temporary:
            self.submit(task, self.complete_request(request))
            summary = task / "source_analysis" / "analysis_summary.json"
            summary.write_text(
                summary.read_text(encoding="utf-8") + " ", encoding="utf-8"
            )
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("source_analysis" in error for error in validation["errors"]))

    def test_runtime_identity_change_is_rejected(self):
        temporary, task, request = self.make_task()
        with temporary:
            self.submit(task, self.complete_request(request))
            with patch(
                "autodraw.agent_tasks.runtime_identity",
                return_value={"app_version": "changed"},
            ):
                validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("运行时身份" in error for error in validation["errors"]))

    def test_production_rejects_operator_record_evidence(self):
        temporary, task, request = self.make_task()
        with temporary:
            candidate = self.complete_request(request)
            candidate["execution"]["mode"] = "production"
            self.submit(task, candidate)
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("operator_record" in error for error in validation["errors"]))

    def test_geometry_acknowledgement_cannot_change_value(self):
        temporary, task, request = self.make_task()
        with temporary:
            candidate = self.complete_request(request)
            candidate["geometry_review"]["fields"]["1"]["MD2"] = 21.0
            self.submit(task, candidate)
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("禁止修改几何" in error for error in validation["errors"]))

    def test_every_evidence_item_requires_disposition(self):
        temporary, task, request = self.make_task()
        with temporary:
            candidate = self.complete_request(request)
            candidate["user_evidence"].append(
                {"id": "forgotten", "kind": "operator_record", "content": "unprocessed"}
            )
            self.submit(task, candidate)
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("尚未分析" in error for error in validation["errors"]))

    def test_human_review_records_artifact_hashes_and_completes_test_task(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            result = task / "result"
            render = task / "validation_render"
            result.mkdir(parents=True)
            render.mkdir()
            pdf = result / "drawing.pdf"
            sheet = render / "contact_sheet_1.png"
            pdf.write_bytes(b"pdf")
            sheet.write_bytes(b"png")
            expected_pdf_hash = _sha256(pdf)
            expected_sheet_hash = _sha256(sheet)
            (result / "audit.json").write_text(
                json.dumps(
                    {
                        "production_release_ready": False,
                        "rendered_pdfs": [str(pdf)],
                        "excluded_components": [],
                        "geometry_warnings": [],
                    }
                ),
                encoding="utf-8",
            )
            (task / "agent_request.json").write_text(
                json.dumps({"execution": {"mode": "test"}}),
                encoding="utf-8",
            )
            (task / "task_state.json").write_text(
                json.dumps(
                    {
                        "task_id": "task",
                        "status": "awaiting_human_review",
                        "status_note": "waiting",
                        "result_dir": str(result),
                        "request_hash": "request-hash",
                        "source_file": "input.zmx",
                        "source_sha256": "a" * 64,
                        "history": [],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "autodraw.output_validation.validate",
                return_value={
                    "all_checks_passed": True,
                    "human_visual_review": {"contact_sheets": [str(sheet)]},
                },
            ):
                delivery = record_human_visual_review(
                    task,
                    status="passed",
                    reviewer="operator-001",
                    note="All pages manually inspected.",
                )
            review = json.loads(
                (task / "human_visual_review.json").read_text(encoding="utf-8")
            )
            state = json.loads((task / "task_state.json").read_text(encoding="utf-8"))

        self.assertTrue(delivery["completed"])
        self.assertEqual(review["review_kind"], "human_operator")
        self.assertEqual(review["contact_sheets"][0]["sha256"], expected_sheet_hash)
        self.assertEqual(review["pdfs"][0]["sha256"], expected_pdf_hash)
        self.assertEqual(state["status"], "completed")


if __name__ == "__main__":
    unittest.main()
