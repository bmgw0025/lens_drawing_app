from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app_version import AGENT_INTERFACE_VERSION
from autodraw.agent_tasks import (
    AgentTaskError,
    create_agent_task,
    get_capabilities,
    get_task_status,
    record_human_visual_review,
    run_agent_task,
    submit_agent_request,
    validate_agent_request,
)
from autodraw.renderer_adapter import DEFAULT_RENDERER_ROOT
from autodraw.runtime import runtime_identity
from autodraw.spec import build_agent_spec, spec_sha256


class AgentCliArgumentError(ValueError):
    """Raised for argument errors that must be returned as structured JSON."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):  # pragma: no cover - exercised through main()
        raise AgentCliArgumentError(message)


def _usable_stream(stream):
    """PyInstaller windowed builds may expose ``None`` for stdout/stderr."""
    return stream if stream is not None and hasattr(stream, "write") else None


def _extract_output_json(argv: list[str]) -> tuple[list[str], Path | None]:
    """Accept --output-json before or after the subcommand."""
    cleaned: list[str] = []
    output_path: Path | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--output-json":
            if index + 1 >= len(argv):
                raise AgentCliArgumentError("--output-json 需要一个文件路径")
            if output_path is not None:
                raise AgentCliArgumentError("--output-json 只能指定一次")
            output_path = Path(argv[index + 1]).expanduser().resolve()
            index += 2
            continue
        if token.startswith("--output-json="):
            value = token.split("=", 1)[1]
            if not value:
                raise AgentCliArgumentError("--output-json 需要一个文件路径")
            if output_path is not None:
                raise AgentCliArgumentError("--output-json 只能指定一次")
            output_path = Path(value).expanduser().resolve()
            index += 1
            continue
        cleaned.append(token)
        index += 1
    return cleaned, output_path


def _write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _emit(payload: dict, output_path: Path | None) -> None:
    """Write the same UTF-8 envelope to a file and/or the available console."""
    if output_path is not None:
        _write_json_file(output_path, payload)
        if getattr(sys, "frozen", False):
            return
    stream = _usable_stream(sys.stdout)
    if stream is not None:
        try:
            stream.write(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
            )
            stream.flush()
        except OSError:
            if output_path is None:
                raise


def _error_envelope(command: str | None, exc: BaseException, code: int = 1) -> dict:
    return {
        "ok": False,
        "command": command,
        "exit_code": code,
        "interface_version": AGENT_INTERFACE_VERSION,
        "error": {"type": type(exc).__name__, "message": str(exc)},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        description="Agent 接管 Lens Drawing 的版本化任务接口。"
    )
    parser.add_argument(
        "--output-json",
        metavar="PATH",
        help="将结构化结果原子写入指定 UTF-8 JSON 文件",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capabilities", help="输出 Agent 可发现的接口与字段目录")
    subparsers.add_parser("spec", help="输出权威 Agent 功能规范与运行时身份")

    create = subparsers.add_parser("create", help="只读分析 ZMX 并创建任务目录")
    create.add_argument("zmx")
    create.add_argument("task_dir")
    create.add_argument("--renderer-root", default=str(DEFAULT_RENDERER_ROOT))

    validate = subparsers.add_parser("validate", help="校验 Agent 填写的需求包")
    validate.add_argument("task_dir")

    submit = subparsers.add_parser("submit", help="原子提交一版 Agent 请求并保留版本历史")
    submit.add_argument("task_dir")
    submit.add_argument("request_file")

    run = subparsers.add_parser("run", help="执行已通过校验的任务")
    run.add_argument("task_dir")

    status = subparsers.add_parser("status", help="读取跨对话权威任务状态")
    status.add_argument("task_dir")

    review = subparsers.add_parser("review", help="记录人工操作员对全部 PDF 页面的视觉验收")
    review.add_argument("task_dir")
    review.add_argument("--status", required=True, choices=("passed", "failed"))
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    output_path: Path | None = None
    command: str | None = None
    try:
        raw_argv, output_path = _extract_output_json(raw_argv)
        args = build_parser().parse_args(raw_argv)
        command = args.command
        if output_path is None and args.output_json:
            output_path = Path(args.output_json).expanduser().resolve()
        if args.command == "capabilities":
            payload = get_capabilities()
            code = 0
        elif args.command == "spec":
            spec = build_agent_spec()
            payload = {
                "spec": spec,
                "spec_sha256": spec_sha256(spec),
                "runtime_identity": runtime_identity(),
            }
            code = 0
        elif args.command == "create":
            payload = create_agent_task(
                args.zmx, args.task_dir, renderer_root=args.renderer_root
            )
            code = 2 if payload["status"] == "blocked_geometry" else 0
        elif args.command == "submit":
            payload = submit_agent_request(args.task_dir, args.request_file)
            code = 0
        elif args.command == "validate":
            payload = validate_agent_request(args.task_dir)
            code = 0 if payload["valid"] else 2
        elif args.command == "run":
            payload = run_agent_task(args.task_dir)
            state = get_task_status(args.task_dir)
            payload = {"state": state, "audit": payload}
            code = 0 if state["status"] in {"awaiting_human_review", "completed"} else 2
        elif args.command == "review":
            payload = record_human_visual_review(
                args.task_dir,
                status=args.status,
                reviewer=args.reviewer,
                note=args.note,
            )
            code = 0 if payload["completed"] else 2
        else:
            payload = get_task_status(args.task_dir)
            code = 0
        envelope = {
            "ok": code == 0,
            "command": command,
            "exit_code": code,
            "interface_version": AGENT_INTERFACE_VERSION,
            "result": payload,
        }
    except (AgentTaskError, AgentCliArgumentError, ValueError, OSError) as exc:
        envelope = _error_envelope(command, exc)
        code = 1
    except Exception as exc:  # Keep the installed boundary machine-readable.
        envelope = _error_envelope(command, exc)
        code = 1
    try:
        _emit(envelope, output_path)
    except Exception as exc:
        fallback = _error_envelope(command, exc)
        stream = _usable_stream(sys.stderr)
        if stream is not None:
            try:
                stream.write(json.dumps(fallback, ensure_ascii=False) + "\n")
                stream.flush()
            except OSError:
                pass
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
