"""Generate a task from a public site, then run it in the original EasySpider executor.

Model decisions are deterministic test stubs. No model API key is needed.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

import _bootstrap
from agent import explore
from executor_smoke import EXECUTOR, SANDBOX
from public_browser_cases import select_for_site
from public_cases import CASES


EXPECTED_ROWS = {"Quotes to Scrape": 100, "Books to Scrape": 1000}
TIMEOUTS = {"Quotes to Scrape": 420, "Books to Scrape": 1200}


def run(case):
    def fake_model(*args):
        return select_for_site(case["name"], *args)

    with patch("agent.ask_json", side_effect=fake_model):
        result = explore(case["name"], {"start_url": case["url"],
                         "task_name": case["name"], "pagination": "until_end"},
                         "deepseek", "stub", "", lambda _message: None)
    return execute_compiled_task(case, result["task"], result["samples"][0])


def execute_compiled_task(case, source_task, sample):
    junction = SANDBOX / "EasySpider" / "resources" / "app" / "chrome_win64"
    if sys.platform != "win32" or not EXECUTOR.is_file() or not junction.is_dir():
        raise RuntimeError("需要 Windows 打包执行器及 README 中的隔离资源目录连接")

    task_id = int(time.time())
    task = dict(source_task)
    task.update({"id": task_id, "saveName": "public_executor_case", "recordLog": True})
    instances = SANDBOX / "execution_instances"
    instances.mkdir(parents=True, exist_ok=True)
    (instances / f"{task_id}.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    (SANDBOX / "config.json").write_text(json.dumps({
        "webserver_address": "http://127.0.0.1", "webserver_port": 8074,
        "user_data_folder": "./user_data", "debug": False,
        "mysql_config_path": "./mysql_config.json",
        "absolute_user_data_folder": str(SANDBOX / "user_data")}, ensure_ascii=False), encoding="utf-8")

    command = [str(EXECUTOR), "--ids", f"[{task_id}]", "--read_type", "local",
               "--headless", "1", "--keyboard", "0"]
    log_path = SANDBOX / f"public_executor_{task_id}.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=SANDBOX, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        try:
            process.wait(timeout=TIMEOUTS[case["name"]])
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait(timeout=10)
            raise RuntimeError(f"执行超时；日志：{log_path}") from exc

    csv_files = list((SANDBOX / "Data" / f"Task_{task_id}").glob("*.csv"))
    if process.returncode != 0 or len(csv_files) != 1:
        log_tail = log_path.read_bytes().decode("utf-8", "replace")[-7000:]
        raise RuntimeError(f"退出码 {process.returncode}；CSV {csv_files}；日志：\n{log_tail}")
    with csv_files[0].open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    expected = EXPECTED_ROWS[case["name"]]
    if len(rows) != expected or any(not all(row.values()) for row in rows):
        raise AssertionError(f"预期 {expected} 条完整记录，实际 {len(rows)} 条；日志：{log_path}；CSV：{csv_files[0]}")
    if any(rows[0].get(name) != value for name, value in sample.items()):
        raise AssertionError(f"首条数据与浏览器试采不同：{rows[0]} vs {sample}")
    return {"site": case["name"], "rows": len(rows), "first": rows[0],
            "last": rows[-1], "log": str(log_path), "csv": str(csv_files[0])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("site", choices=("quotes", "books"))
    name = "Quotes to Scrape" if parser.parse_args().site == "quotes" else "Books to Scrape"
    chosen = next(case for case in CASES if case["name"] == name)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run(chosen), ensure_ascii=False, indent=2))
