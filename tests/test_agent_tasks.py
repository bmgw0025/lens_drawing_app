from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autodraw.agent_tasks import (
    AgentTaskError,
    create_agent_task,
    record_visual_review,
    resolve_agent_geometry,
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
        system.zosapi_paths = {
            "zemax_root": r"C:\Users\Administrator\Documents\Zemax",
            "opticstudio_install_dir": (
                r"C:\Program Files\Ansys Zemax OpticStudio 2022 R2.01"
            ),
        }
        return system


class LowConfidenceMdProvider(FakeProvider):
    def extract(self, source):
        system = super().extract(source)
        for surface in system.surfaces:
            if surface.index in {4, 5}:
                surface.solves["mechanical_semi_diameter"] = "Automatic"
        return system


def write_approved_policy(path: Path, *, review_mode: str = "vision_agent") -> Path:
    source = (
        Path(__file__).resolve().parents[1]
        / "agent_resources"
        / "deployment_policy.example.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload.update(
        {
            "approval_status": "approved",
            "approved_by": "unit-test",
            "approved_at": "2026-08-18T10:00:00+08:00",
        }
    )
    payload["visual_review"]["mode"] = review_mode
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class AgentTaskContractTests(unittest.TestCase):
    def make_task(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "input.zmx"
        source.write_text("synthetic zmx", encoding="ascii")
        policy = write_approved_policy(root / "deployment_policy.json")
        task = root / "task"
        with patch("autodraw.agent_tasks.NativeZosApiProvider", FakeProvider):
            state = create_agent_task(
                source,
                task,
                renderer_root=DEFAULT_RENDERER_ROOT,
                deployment_policy=policy,
            )
        self.assertEqual(state["status"], "awaiting_geometry_resolution")
        self.resolve_with_preferred_candidates(task)
        request = json.loads((task / "agent_request.json").read_text(encoding="utf-8"))
        return temporary, task, request

    @staticmethod
    def geometry_decision(task: Path):
        cases = json.loads(
            (task / "source_analysis" / "geometry_cases.json").read_text(
                encoding="utf-8"
            )
        )
        decisions = []
        for case in cases["cases"]:
            if not case["resolution_required"]:
                continue
            selections = {}
            for field in case["required_field_selections"]:
                candidates = [
                    item
                    for item in case["field_candidates"][field]
                    if item["eligible"]
                ]
                if field.startswith("Lens"):
                    preferred = next(
                        (
                            item
                            for item in candidates
                            if item.get("association") == "current_lens_boundary"
                        ),
                        candidates[0],
                    )
                else:
                    preferred = next(
                        (
                            item
                            for item in candidates
                            if any(
                                source.get("meets_current_ad_constraint")
                                for source in item.get("sources", [])
                            )
                        ),
                        candidates[0],
                    )
                selections[field] = preferred["candidate_id"]
            topology = next(
                (
                    item["candidate_id"]
                    for item in case["topology_candidates"]
                    if item["eligible"]
                ),
                None,
            )
            decisions.append(
                {
                    "case_id": case["case_id"],
                    "topology_candidate_id": topology,
                    "field_selections": selections,
                    "reason": "Unit-test selection from frozen ZOS-API candidates.",
                }
            )
        return {
            "schema_version": "1.0",
            "task_id": cases["task_id"],
            "cases": decisions,
        }

    @classmethod
    def resolve_with_preferred_candidates(cls, task: Path):
        decision = cls.geometry_decision(task)
        path = task.parent / "geometry-decision.json"
        path.write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")
        state = resolve_agent_geometry(task, path)
        if state["status"] not in {"needs_input", "needs_clarification"}:
            raise AssertionError(state)
        return decision

    @staticmethod
    def complete_request(request, *, include_geometry_confirmations=True):
        request = copy.deepcopy(request)
        request["user_evidence"] = [
            {
                "id": "op",
                "kind": "operator_record",
                "content": "test mode naming and requirement analysis",
                "captured_at": "2026-08-18T10:00:00+08:00",
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
                    "category": "manufacturing_complete",
                    "statement": "Use deployment defaults with no special overrides.",
                    "evidence_ids": ["op"],
                },
            ],
            "evidence_disposition": {
                "op": {
                    "status": "mapped",
                    "targets": [
                        "naming",
                        "manufacturing.deployment_policy",
                    ],
                    "explanation": "The test record supplies naming and confirms no overrides.",
                }
            },
            "assumptions": [],
            "unresolved_questions": [],
        }
        if include_geometry_confirmations:
            task = (
                Path(request["source"]["zmx_path"]).parent
                / request["task_id"]
            )
            state = json.loads(
                (task / "task_state.json").read_text(encoding="utf-8")
            )
            for requirement in state.get("required_geometry_confirmations", []):
                confirmation_id = requirement["confirmation_id"]
                request["requirement_analysis"]["decisions"].append(
                    {
                        "category": "geometry_confirmation",
                        "confirmation_id": confirmation_id,
                        "statement": "The test record confirms this geometry candidate.",
                        "evidence_ids": ["op"],
                    }
                )
                request["requirement_analysis"]["evidence_disposition"]["op"][
                    "targets"
                ].append(f"geometry_confirmation.{confirmation_id}")
        request["naming"] = {
            "mode": "generated",
            "confirm_generated_names": True,
            "evidence_ids": ["op"],
        }
        request["execution"]["mode"] = "test"
        return request

    @staticmethod
    def submit(task, request):
        candidate = task.parent / "candidate.json"
        candidate.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        submit_agent_request(task, candidate)

    def test_create_freezes_policy_and_requires_virtual_geometry_resolution(self):
        temporary, task, request = self.make_task()
        with temporary:
            state = json.loads((task / "task_state.json").read_text(encoding="utf-8"))
            cases = json.loads(
                (task / "source_analysis" / "geometry_cases.json").read_text(
                    encoding="utf-8"
                )
            )
            resolution = json.loads(
                (task / "geometry_resolution.json").read_text(encoding="utf-8")
            )
        self.assertEqual(state["status"], "needs_clarification")
        self.assertTrue(state["required_geometry_confirmations"])
        self.assertEqual(request["schema_version"], "1.2")
        self.assertTrue(cases["resolution_required"])
        self.assertEqual(
            cases["cases"][0]["required_field_selections"],
            ["Lens1.AD_right", "Lens2.AD_left", "MD1", "MD2"],
        )
        self.assertEqual(resolution["task_id"], state["task_id"])
        self.assertEqual(
            request["manufacturing_requirements"]["approval_source"],
            "deployment_policy",
        )
        self.assertEqual(
            request["execution"]["visual_review"]["mode"], "vision_agent"
        )

    def test_complete_test_request_validates(self):
        temporary, task, request = self.make_task()
        with temporary:
            self.submit(task, self.complete_request(request))
            validation = validate_agent_request(task)
        self.assertTrue(validation["valid"], validation["errors"])

    def test_low_confidence_md_requires_user_confirmation(self):
        temporary = tempfile.TemporaryDirectory()
        with temporary:
            root = Path(temporary.name)
            source = root / "input.zmx"
            source.write_text("zmx", encoding="ascii")
            policy = write_approved_policy(root / "policy.json")
            task = root / "task"
            with patch(
                "autodraw.agent_tasks.NativeZosApiProvider",
                LowConfidenceMdProvider,
            ):
                create_agent_task(
                    source,
                    task,
                    renderer_root=DEFAULT_RENDERER_ROOT,
                    deployment_policy=policy,
                )
            decision = self.geometry_decision(task)
            decision_path = root / "geometry-decision.json"
            decision_path.write_text(
                json.dumps(decision, ensure_ascii=False),
                encoding="utf-8",
            )
            state = resolve_agent_geometry(task, decision_path)
            request = json.loads(
                (task / "agent_request.json").read_text(encoding="utf-8")
            )
            requirements = state["required_geometry_confirmations"]

            self.assertEqual(state["status"], "needs_clarification")
            self.assertTrue(requirements)
            self.assertTrue(
                any(item["field"] == "MD2" for item in requirements),
                requirements,
            )
            self.assertTrue(
                all(item["prompt"] in state["unresolved_questions"] for item in requirements)
            )

            candidate = self.complete_request(
                request,
                include_geometry_confirmations=False,
            )
            candidate["user_evidence"][0]["kind"] = "user_message"
            candidate["execution"]["mode"] = "production"
            self.submit(task, candidate)
            validation = validate_agent_request(task)
            self.assertFalse(validation["valid"])
            self.assertTrue(
                any("低置信几何尚未取得用户确认" in error for error in validation["errors"]),
                validation["errors"],
            )

            confirmed = copy.deepcopy(candidate)
            for requirement in requirements:
                confirmation_id = requirement["confirmation_id"]
                confirmed["requirement_analysis"]["decisions"].append(
                    {
                        "category": "geometry_confirmation",
                        "confirmation_id": confirmation_id,
                        "statement": "The internal technician confirmed this candidate.",
                        "evidence_ids": ["op"],
                    }
                )
                confirmed["requirement_analysis"]["evidence_disposition"]["op"][
                    "targets"
                ].append(f"geometry_confirmation.{confirmation_id}")
            self.submit(task, confirmed)
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

    def test_deployment_policy_tamper_is_rejected(self):
        temporary, task, request = self.make_task()
        with temporary:
            self.submit(task, self.complete_request(request))
            policy = task / "deployment_policy.json"
            policy.write_text(
                policy.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
            validation = validate_agent_request(task)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("deployment_policy.json 已被修改" in e for e in validation["errors"]))

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

    def test_geometry_decision_rejects_numeric_mutation_field(self):
        temporary = tempfile.TemporaryDirectory()
        with temporary:
            root = Path(temporary.name)
            source = root / "input.zmx"
            source.write_text("zmx", encoding="ascii")
            policy = write_approved_policy(root / "policy.json")
            task = root / "task"
            with patch("autodraw.agent_tasks.NativeZosApiProvider", FakeProvider):
                create_agent_task(
                    source,
                    task,
                    renderer_root=DEFAULT_RENDERER_ROOT,
                    deployment_policy=policy,
                )
            decision = self.geometry_decision(task)
            decision["cases"][0]["MD1"] = 20.0
            path = root / "bad.json"
            path.write_text(json.dumps(decision), encoding="utf-8")
            with self.assertRaisesRegex(AgentTaskError, "禁止字段"):
                resolve_agent_geometry(task, path)

    def test_geometry_decision_rejects_unknown_candidate(self):
        temporary = tempfile.TemporaryDirectory()
        with temporary:
            root = Path(temporary.name)
            source = root / "input.zmx"
            source.write_text("zmx", encoding="ascii")
            policy = write_approved_policy(root / "policy.json")
            task = root / "task"
            with patch("autodraw.agent_tasks.NativeZosApiProvider", FakeProvider):
                create_agent_task(
                    source,
                    task,
                    renderer_root=DEFAULT_RENDERER_ROOT,
                    deployment_policy=policy,
                )
            decision = self.geometry_decision(task)
            decision["cases"][0]["field_selections"]["MD1"] = "ad-g1-l1-right-s3"
            path = root / "bad.json"
            path.write_text(json.dumps(decision), encoding="utf-8")
            with self.assertRaisesRegex(AgentTaskError, "未知候选"):
                resolve_agent_geometry(task, path)

    def test_geometry_decision_rejects_md_smaller_than_selected_ad(self):
        temporary = tempfile.TemporaryDirectory()
        with temporary:
            root = Path(temporary.name)
            source = root / "input.zmx"
            source.write_text("zmx", encoding="ascii")
            policy = write_approved_policy(root / "policy.json")
            task = root / "task"
            with patch("autodraw.agent_tasks.NativeZosApiProvider", FakeProvider):
                create_agent_task(
                    source,
                    task,
                    renderer_root=DEFAULT_RENDERER_ROOT,
                    deployment_policy=policy,
                )
            decision = self.geometry_decision(task)
            cases = json.loads(
                (task / "source_analysis" / "geometry_cases.json").read_text(
                    encoding="utf-8"
                )
            )
            small = next(
                item
                for item in cases["cases"][0]["field_candidates"]["MD2"]
                if item["diameter_mm"] == 16.0
            )
            decision["cases"][0]["field_selections"]["MD2"] = small["candidate_id"]
            path = root / "bad.json"
            path.write_text(json.dumps(decision), encoding="utf-8")
            with self.assertRaisesRegex(AgentTaskError, "小于所选两侧 AD"):
                resolve_agent_geometry(task, path)

    def test_vision_review_records_artifact_hashes_and_completes_test_task(self):
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
            pdf_hash = _sha256(pdf)
            sheet_hash = _sha256(sheet)
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
            (result / "pdf_validation_report.json").write_text(
                json.dumps(
                    {
                        "automated_checks_passed": True,
                        "visual_review": {"contact_sheets": [str(sheet)]},
                    }
                ),
                encoding="utf-8",
            )
            (task / "agent_request.json").write_text(
                json.dumps(
                    {
                        "execution": {
                            "mode": "test",
                            "visual_review": {"mode": "vision_agent"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (task / "task_state.json").write_text(
                json.dumps(
                    {
                        "task_id": "task",
                        "status": "awaiting_visual_review",
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
            external = task / "vision-report.json"
            external.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "status": "passed",
                        "review_kind": "vision_agent",
                        "model": "minimax-m3",
                        "prompt_version": "lens-visual-review-1",
                        "duration_ms": 1234,
                        "issues": [],
                        "uncertainties": [],
                        "artifacts": {
                            "contact_sheets": [
                                {"path": str(sheet), "sha256": sheet_hash}
                            ],
                            "pdfs": [{"path": str(pdf), "sha256": pdf_hash}],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "autodraw.output_validation.validate",
                return_value={"all_checks_passed": True},
            ):
                delivery = record_visual_review(
                    task,
                    status="passed",
                    kind="vision_agent",
                    reviewer="minimax-m3@test",
                    report_file=external,
                    note="Automated page review passed.",
                )
            review = json.loads(
                (task / "visual_review.json").read_text(encoding="utf-8")
            )
            state = json.loads((task / "task_state.json").read_text(encoding="utf-8"))

        self.assertTrue(delivery["completed"])
        self.assertEqual(review["review_kind"], "vision_agent")
        self.assertEqual(review["contact_sheets"][0]["sha256"], sheet_hash)
        self.assertEqual(review["pdfs"][0]["sha256"], pdf_hash)
        self.assertEqual(state["status"], "completed")


if __name__ == "__main__":
    unittest.main()
