from __future__ import annotations

from pathlib import PureWindowsPath


# These binaries are launchers for the Workbench/ENVI UI or the IDL runtime
# VM. They are not the command-line development interpreter that can compile
# a project-provided .pro batch wrapper. Failing early matters because a
# launcher can exit cleanly without executing the submitted source.
_PROJECT_PRO_UNSUPPORTED_NAMES = {
    "idlde",
    "idlde.exe",
    "envi_idl",
    "envi_idl.exe",
    "envi_classic_idl",
    "envi_classic_idl.exe",
    "idlrt",
    "idlrt.exe",
}


def validate_project_pro_executable(executable: str) -> str | None:
    """Return an actionable error when *executable* cannot run a ``.pro``.

    The runners accept only a configured executable, never a user-supplied
    command line. ``None`` means that the executable name is a valid
    candidate; it does not prove that the binary exists or that a license is
    available.
    """

    value = str(executable or "").strip()
    if not value:
        return None
    normalized = value.replace("/", "\\")
    name = PureWindowsPath(normalized).name.lower()
    if name in _PROJECT_PRO_UNSUPPORTED_NAMES:
        return (
            f"配置的 IDL 入口 {value!r} 是 Workbench/ENVI GUI 或 IDL Runtime 启动器，"
            "不能直接编译项目 .pro。请改用受许可的命令行 idl.exe（或部署节点提供的 "
            "ENVI batch/SAV 入口）；这不会影响 PythonRunner。"
        )
    return None
