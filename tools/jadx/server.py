# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = [ "fastmcp>=3.0.2" ]
# ///

"""
JADX MCP Server - Headless CLI Edition
基于 JADX CLI 官方命令行引擎实现，无需 JADX-GUI 介入。
支持全自动将 APK 反编译为 Java 源码工程，检索类、方法、源码与清单文件。
"""

import os
import sys
import argparse
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any

os.environ["FASTMCP_LOG_ENABLED"] = "false"
from fastmcp import FastMCP

# Bootstrap logger — always writes to stderr to keep stdout clean for stdio transport
logger = logging.getLogger("jadx-mcp-server")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

# Parse arguments
parser = argparse.ArgumentParser("JADX Headless MCP Server")
parser.add_argument("--http", help="Serve MCP Server over HTTP stream.", action="store_true", default=False)
parser.add_argument("--host", help="Host address to bind for --http (default: 127.0.0.1)", default="127.0.0.1", type=str)
parser.add_argument("--port", help="Port for --http (default:8651)", default=8651, type=int)
parser.add_argument("--workspace", help="Workspace directory for decompiled projects", default="tools/workspace/jadx", type=str)
parser.add_argument("--jadx-path", help="Path to jadx.bat or jadx binary", default=None, type=str)
args, _ = parser.parse_known_args()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE_DIR = Path(args.workspace) if os.path.isabs(args.workspace) else PROJECT_ROOT / args.workspace
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

# Determine jadx path
if args.jadx_path:
    JADX_BIN = Path(args.jadx_path)
else:
    local_jadx = PROJECT_ROOT / "tools" / "bin" / "jadx" / "bin" / "jadx.bat"
    if local_jadx.exists():
        JADX_BIN = local_jadx
    else:
        which_jadx = shutil.which("jadx")
        JADX_BIN = Path(which_jadx) if which_jadx else local_jadx

# Ensure embedded JRE is in JAVA_HOME if available
embedded_jre = PROJECT_ROOT / "tools" / "bin" / "jre"
if embedded_jre.exists() and "JAVA_HOME" not in os.environ:
    os.environ["JAVA_HOME"] = str(embedded_jre)

mcp = FastMCP("JADX-Headless-MCP-Server")


@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Check JADX CLI availability and environment status."""
    jadx_exists = JADX_BIN.exists()
    version = "unknown"
    if jadx_exists:
        try:
            res = subprocess.run([str(JADX_BIN), "--version"], capture_output=True, text=True, timeout=10)
            version = res.stdout.strip() or res.stderr.strip()
        except Exception as e:
            version = f"error: {e}"
    return {
        "success": jadx_exists,
        "jadx_path": str(JADX_BIN),
        "jadx_exists": jadx_exists,
        "jadx_version": version,
        "workspace": str(WORKSPACE_DIR),
        "java_home": os.environ.get("JAVA_HOME")
    }


@mcp.tool()
async def decompile_apk(
    apk_path: str,
    project_name: Optional[str] = None,
    deobfuscation: bool = False,
    threads_count: int = 4
) -> Dict[str, Any]:
    """
    Decompile an APK file directly to Java source code using JADX CLI without GUI.

    Args:
        apk_path: Path to the APK file to decompile
        project_name: Optional project folder name (defaults to apk file name)
        deobfuscation: Whether to enable deobfuscation (--deobf)
        threads_count: Number of processing threads (default: 4)
    """
    apk_file = Path(apk_path)
    if not apk_file.exists():
        apk_file = PROJECT_ROOT / apk_path
        if not apk_file.exists():
            return {"success": False, "error": f"APK not found: {apk_path}"}

    if not project_name:
        project_name = apk_file.stem

    out_dir = WORKSPACE_DIR / project_name
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [str(JADX_BIN), "-d", str(out_dir), "-j", str(threads_count)]
    if deobfuscation:
        cmd.append("--deobf")
    cmd.append(str(apk_file))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return {
            "success": proc.returncode == 0,
            "project_dir": str(out_dir),
            "stdout": proc.stdout[-2000:] if proc.stdout else "",
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
            "exit_code": proc.returncode
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to run JADX: {str(e)}"}


@mcp.tool()
async def get_class_source(project_dir: str, class_name: str) -> Dict[str, Any]:
    """
    Fetch the Java source code of a specific class.

    Args:
        project_dir: Path to the decompiled project directory
        class_name: Full class name (e.g. 'com.example.MainActivity') or relative path
    """
    proj_path = Path(project_dir)
    if not proj_path.is_absolute():
        proj_path = PROJECT_ROOT / project_dir

    sources_dir = proj_path / "sources"
    if not sources_dir.exists():
        sources_dir = proj_path

    # Try exact match by class name converted to path
    rel_path = class_name.replace(".", "/") + ".java"
    target_file = sources_dir / rel_path

    if not target_file.exists():
        # Case insensitive or partial search
        simple_name = class_name.split(".")[-1] + ".java"
        candidates = list(sources_dir.rglob(simple_name))
        if candidates:
            target_file = candidates[0]

    if target_file.exists():
        try:
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return {
                "success": True,
                "class_name": class_name,
                "file_path": str(target_file),
                "source": content
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to read source: {str(e)}"}

    return {"success": False, "error": f"Class '{class_name}' not found in {sources_dir}"}


@mcp.tool()
async def list_classes(
    project_dir: str,
    package_prefix: Optional[str] = None,
    offset: int = 0,
    count: int = 100
) -> Dict[str, Any]:
    """
    List all decompiled Java classes in the project with pagination.

    Args:
        project_dir: Path to the decompiled project directory
        package_prefix: Optional package filter (e.g. 'com.example')
        offset: Starting offset for pagination
        count: Number of classes to return (default: 100)
    """
    proj_path = Path(project_dir)
    if not proj_path.is_absolute():
        proj_path = PROJECT_ROOT / project_dir

    sources_dir = proj_path / "sources"
    if not sources_dir.exists():
        sources_dir = proj_path

    if not sources_dir.exists():
        return {"success": False, "error": f"Source directory not found: {sources_dir}"}

    all_classes = []
    for root, _, files in os.walk(sources_dir):
        for f in files:
            if f.endswith(".java"):
                full_path = Path(root) / f
                rel_parts = full_path.relative_to(sources_dir).with_suffix("").parts
                fqcn = ".".join(rel_parts)
                if not package_prefix or fqcn.startswith(package_prefix):
                    all_classes.append(fqcn)

    all_classes.sort()
    total = len(all_classes)
    paged = all_classes[offset: offset + count] if count > 0 else all_classes[offset:]

    return {
        "success": True,
        "total": total,
        "offset": offset,
        "count": len(paged),
        "classes": paged
    }


@mcp.tool()
async def search_java_code(
    project_dir: str,
    keyword: str,
    case_sensitive: bool = False,
    max_results: int = 50
) -> Dict[str, Any]:
    """
    Search for a keyword or string pattern across all decompiled Java code.

    Args:
        project_dir: Path to the decompiled project directory
        keyword: String to search for
        case_sensitive: Case sensitivity flag
        max_results: Max matched items to return (default: 50)
    """
    proj_path = Path(project_dir)
    if not proj_path.is_absolute():
        proj_path = PROJECT_ROOT / project_dir

    sources_dir = proj_path / "sources"
    if not sources_dir.exists():
        sources_dir = proj_path

    results = []
    target_kw = keyword if case_sensitive else keyword.lower()

    for root, _, files in os.walk(sources_dir):
        for f in files:
            if f.endswith(".java"):
                file_path = Path(root) / f
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                        lines = file_obj.readlines()
                    for idx, line in enumerate(lines, 1):
                        cmp_line = line if case_sensitive else line.lower()
                        if target_kw in cmp_line:
                            rel_class = ".".join(file_path.relative_to(sources_dir).with_suffix("").parts)
                            results.append({
                                "class": rel_class,
                                "line": idx,
                                "content": line.strip()
                            })
                            if len(results) >= max_results:
                                return {
                                    "success": True,
                                    "results": results,
                                    "truncated": True,
                                    "total_found": len(results)
                                }
                except Exception:
                    continue

    return {
        "success": True,
        "results": results,
        "truncated": False,
        "total_found": len(results)
    }


@mcp.tool()
async def get_android_manifest(project_dir: str) -> Dict[str, Any]:
    """
    Get the AndroidManifest.xml from the decompiled project.

    Args:
        project_dir: Path to the decompiled project directory
    """
    proj_path = Path(project_dir)
    if not proj_path.is_absolute():
        proj_path = PROJECT_ROOT / project_dir

    manifest_candidates = [
        proj_path / "resources" / "AndroidManifest.xml",
        proj_path / "AndroidManifest.xml"
    ]

    for manifest in manifest_candidates:
        if manifest.exists():
            try:
                with open(manifest, "r", encoding="utf-8", errors="ignore") as f:
                    return {
                        "success": True,
                        "manifest_path": str(manifest),
                        "content": f.read()
                    }
            except Exception as e:
                return {"success": False, "error": f"Failed reading manifest: {str(e)}"}

    return {"success": False, "error": "AndroidManifest.xml not found"}


def main():
    if args.http:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
