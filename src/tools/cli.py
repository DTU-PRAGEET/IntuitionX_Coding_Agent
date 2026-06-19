"""CLI for standalone primitive tool invocation."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from src.core.logging import configure_logging, get_logger
from src.tools.registry import ToolRegistry

_log = get_logger("tools_cli")


def _print_result(result) -> int:
    print(json.dumps(result.model_dump(), indent=2))
    return result.exit_code


def _run_tool(name: str, cwd: str, kwargs: dict) -> int:
    configure_logging()
    registry = ToolRegistry(cwd)
    try:
        result = registry.execute(name, **kwargs)
        return _print_result(result)
    finally:
        registry.dispose()


def cmd_demo(_args: argparse.Namespace) -> int:
    configure_logging()
    with tempfile.TemporaryDirectory(prefix="tools_demo_") as tmp:
        cwd = tmp
        registry = ToolRegistry(cwd)
        steps = [
            ("write", {"path": "hello.txt", "content": "hello world\nline two\n"}),
            ("read", {"path": "hello.txt", "start_line": 1}),
            ("edit", {"path": "hello.txt", "old_string": "hello world", "new_string": "hello universe"}),
            ("grep", {"pattern": "universe", "path": "."}),
            ("find", {"pattern": "*.txt", "path": "."}),
            ("ls", {"path": "."}),
            ("bash", {"command": "echo demo_ok"}),
        ]
        failed = False
        try:
            for name, kwargs in steps:
                _log.info("demo_step", tool=name, kwargs=kwargs)
                result = registry.execute(name, **kwargs)
                print(json.dumps({"tool": name, **result.model_dump()}, indent=2))
                if result.is_error:
                    failed = True
                    break
        finally:
            registry.dispose()
        return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sprinklr primitive tools CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    demo_p = sub.add_parser("demo", help="Run write→read→edit→grep→find→ls→bash in temp dir")
    demo_p.set_defaults(func=cmd_demo)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cwd", default=".", help="Workspace root")

    read_p = sub.add_parser("read", parents=[common], help="Read a file")
    read_p.add_argument("--path", required=True)
    read_p.add_argument("--start-line", type=int, default=1)
    read_p.add_argument("--end-line", type=int, default=None)

    write_p = sub.add_parser("write", parents=[common], help="Write a file")
    write_p.add_argument("--path", required=True)
    write_p.add_argument("--content", required=True)

    edit_p = sub.add_parser("edit", parents=[common], help="Edit a file")
    edit_p.add_argument("--path", required=True)
    edit_p.add_argument("--old-string", required=True)
    edit_p.add_argument("--new-string", required=True)

    bash_p = sub.add_parser("bash", parents=[common], help="Run bash command")
    bash_p.add_argument("--shell-cmd", required=True, dest="shell_cmd")
    bash_p.add_argument("--timeout", type=int, default=30)

    find_p = sub.add_parser("find", parents=[common], help="Find files by glob")
    find_p.add_argument("--pattern", required=True)
    find_p.add_argument("--path", default=".")
    find_p.add_argument("--limit", type=int, default=1000)

    grep_p = sub.add_parser("grep", parents=[common], help="Grep for pattern")
    grep_p.add_argument("--pattern", required=True)
    grep_p.add_argument("--path", default=".")
    grep_p.add_argument("--glob-pattern", default="**/*")
    grep_p.add_argument("--limit", type=int, default=100)

    ls_p = sub.add_parser("ls", parents=[common], help="List directory")
    ls_p.add_argument("--path", default=".")
    ls_p.add_argument("--limit", type=int, default=500)

    args = parser.parse_args(argv)
    if args.command == "demo":
        return args.func(args)

    cwd = str(Path(args.cwd).resolve())
    if args.command == "read":
        return _run_tool("read", cwd, {"path": args.path, "start_line": args.start_line, "end_line": args.end_line})
    if args.command == "write":
        return _run_tool("write", cwd, {"path": args.path, "content": args.content})
    if args.command == "edit":
        return _run_tool(
            "edit",
            cwd,
            {"path": args.path, "old_string": args.old_string, "new_string": args.new_string},
        )
    if args.command == "bash":
        return _run_tool("bash", cwd, {"command": args.shell_cmd, "timeout": args.timeout})
    if args.command == "find":
        return _run_tool("find", cwd, {"pattern": args.pattern, "path": args.path, "limit": args.limit})
    if args.command == "grep":
        return _run_tool(
            "grep",
            cwd,
            {
                "pattern": args.pattern,
                "path": args.path,
                "glob_pattern": args.glob_pattern,
                "limit": args.limit,
            },
        )
    if args.command == "ls":
        return _run_tool("ls", cwd, {"path": args.path, "limit": args.limit})
    return 1


if __name__ == "__main__":
    sys.exit(main())
