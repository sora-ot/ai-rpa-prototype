"""Run an AI-generated task in native EasySpider with its design-time profile.

All task copies, configuration, logs, and output stay in executor_sandbox/.
The original EasySpider files are read only.
"""
import argparse
import copy
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from auth import PROFILE_ROOT, profile_path
from workflow import validate_task


BASE = Path(__file__).resolve().parent
SOURCE_ROOT = BASE.parent / "EasySpider" / "ElectronJS"
BUNDLED = SOURCE_ROOT / "chrome_win64"
EXECUTOR = BUNDLED / "easyspider_executestage.exe"
SANDBOX = BASE / "executor_sandbox"
GENERATED = BASE / "generated"


def resolve_task_file(task_id=None, task_file=None):
    if task_id is not None:
        if task_id < 0:
            raise ValueError("任务 ID 必须是非负整数")
        path = SOURCE_ROOT / "execution_instances" / f"{task_id}.json"
    else:
        path = Path(task_file)
        if not path.is_absolute():
            path = BASE / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到任务文件：{path}")
    return path


def resolve_profile(task, explicit=None):
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = PROFILE_ROOT / path
    else:
        path = profile_path(task.get("url", ""))
    path = path.resolve()
    if not path.is_relative_to(PROFILE_ROOT.resolve()):
        raise ValueError("资料目录必须位于 ai-rpa-prototype/profiles 内")
    if not (path / "Default").is_dir():
        raise FileNotFoundError(f"AI 设计阶段的浏览器资料目录不存在：{path}")
    if not ((path / "Default" / "Network" / "Cookies").is_file() or
            (path / "Default" / "Cookies").is_file() or
            (path / "Default" / "Local Storage").is_dir()):
        raise FileNotFoundError(f"资料目录没有会话存储；请先在 AI 设计浏览器中登录：{path}")
    return path


def verify_bundled_executor():
    if sys.platform != "win32" or not EXECUTOR.is_file():
        raise RuntimeError("本命令需要 Windows 版 EasySpider 自带执行器")
    linked = SANDBOX / "EasySpider" / "resources" / "app" / "chrome_win64"
    if not linked.is_dir() or (linked / "chrome.exe").resolve() != (BUNDLED / "chrome.exe").resolve():
        raise RuntimeError("缺少执行器隔离资源目录连接，请按 README 建立 executor_sandbox 下的 Chrome 目录连接")


def filter_nonrecord_cards(task):
    """Upgrade a previously saved list task to skip cards without its first field."""
    task = copy.deepcopy(task)
    graph = task["graph"]
    replacements = {}
    guarded_lists = 0
    for node in graph:
        params = node.get("parameters", {})
        if node.get("option") != 8 or params.get("loopType") != 1:
            continue
        extraction_nodes = [graph[index] for index in node.get("sequence", [])
                            if graph[index].get("option") == 3]
        if not extraction_nodes:
            continue
        fields = extraction_nodes[0].get("parameters", {}).get("params", [])
        anchors = [str(field.get("relativeXPath", "")) for field in fields
                   if field.get("relative") and
                   str(field.get("relativeXPath", "")).startswith("/")]
        base = params.get("xpath", "")
        if not base or not anchors:
            continue
        if any(base.endswith(f")[.{anchor}]") for anchor in anchors):
            guarded_lists += 1
            continue
        scoped = "." + anchors[0]
        guarded = f"({base})[{scoped}]"
        params["xpath"] = guarded
        replacements[base] = guarded
        guarded_lists += 1
    for node in graph:
        params = node.get("parameters", {})
        if params.get("exitElement") in replacements:
            params["exitElement"] = replacements[params["exitElement"]]
    if not guarded_lists:
        raise ValueError("任务没有可用的列表字段定位器，无法过滤非商品卡片")
    validate_task(task)
    return task


def run(task_path, profile, *, headless=False, timeout=900, dry_run=False,
        filter_nonrecords=False):
    verify_bundled_executor()
    task = json.loads(task_path.read_text(encoding="utf-8"))
    validate_task(task)
    profile = resolve_profile(task, profile)
    if filter_nonrecords:
        task = filter_nonrecord_cards(task)
    run_id = int(time.time_ns() // 1_000_000)
    command = [str(EXECUTOR), "--ids", f"[{run_id}]", "--read_type", "local",
               "--headless", "1" if headless else "0", "--keyboard", "0",
               "--user_data", "1", "--user_folder", str(profile)]
    summary = {"source_task": str(task_path), "profile": str(profile),
               "browser": str(BUNDLED / "chrome.exe"), "run_id": run_id,
               "command": command}
    if dry_run:
        return summary

    SANDBOX.mkdir(exist_ok=True)
    instances = SANDBOX / "execution_instances"
    instances.mkdir(exist_ok=True)
    local_task = copy.deepcopy(task)
    local_task.update({"id": run_id, "saveName": f"ai_profile_{run_id}",
                       "recordLog": True, "dataWriteMode": 3, "startFromExit": 0})
    if filter_nonrecords:
        GENERATED.mkdir(exist_ok=True)
        corrected = GENERATED / f"easyspider_ai_filtered_{run_id}.json"
        corrected.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["corrected_task"] = str(corrected)
    (instances / f"{run_id}.json").write_text(json.dumps(local_task, ensure_ascii=False),
                                                encoding="utf-8")
    (SANDBOX / "config.json").write_text(json.dumps({
        "webserver_address": "http://127.0.0.1", "webserver_port": 8074,
        "user_data_folder": "./user_data", "debug": False,
        "mysql_config_path": "./mysql_config.json",
        "absolute_user_data_folder": str(profile)}, ensure_ascii=False), encoding="utf-8")
    log_path = SANDBOX / f"native_profile_{run_id}.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=SANDBOX, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait(timeout=10)
            raise TimeoutError(f"原版执行器超时，日志：{log_path}") from exc
    output_dir = SANDBOX / "Data" / f"Task_{run_id}"
    suffix = {"csv": ".csv", "json": ".json", "txt": ".txt", "xlsx": ".xlsx"}[
        local_task["outputFormat"]]
    files = list(output_dir.glob("*" + suffix))
    if process.returncode != 0 or len(files) != 1:
        tail = log_path.read_bytes().decode("utf-8", "replace")[-3000:]
        raise RuntimeError(f"原版执行器退出码 {process.returncode}，输出文件 {files}；日志：{log_path}\n{tail}")
    summary.update({"exit_code": process.returncode, "output": str(files[0]),
                    "log": str(log_path)})
    if suffix == ".csv":
        with files[0].open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        summary["rows"] = len(rows)
        summary["nonempty_rows"] = sum(all(str(value).strip() for value in row.values())
                                       for row in rows)
    return summary


def main():
    parser = argparse.ArgumentParser(description="使用 AI 设计时的资料目录运行原版 EasySpider")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task-id", type=int, help="原版 execution_instances 中的任务 ID")
    source.add_argument("--task-file", help="相对于 ai-rpa-prototype 的生成任务 JSON")
    parser.add_argument("--profile", help="AI 资料目录名称或 ai-rpa-prototype/profiles 下的绝对路径")
    parser.add_argument("--headless", action="store_true", help="隐藏测试浏览器")
    parser.add_argument("--filter-nonrecords", action="store_true",
                        help="为旧流程的列表循环加入第一字段存在条件，排除广告或问卷卡片")
    parser.add_argument("--timeout", type=int, default=900, help="执行超时秒数")
    parser.add_argument("--dry-run", action="store_true", help="只检查任务与资料目录")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须为正数")
    try:
        path = resolve_task_file(args.task_id, args.task_file)
        result = run(path, args.profile, headless=args.headless,
                     timeout=args.timeout, dry_run=args.dry_run,
                     filter_nonrecords=args.filter_nonrecords)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        parser.exit(1, f"错误：{exc}\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
