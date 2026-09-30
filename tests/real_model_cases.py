"""Manual two-stage checks with the configured model API; no secrets are saved.

Run ``plan CASE`` first and inspect generated/real_plan_CASE.json. Run
``run CASE`` only after confirming the saved plan is the intended public site.
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from _bootstrap import ROOT
from agent import explore, make_plan
from public_cases import CASES as PUBLIC_CASES
from public_executor_cases import execute_compiled_task
from settings import resolve_model_config


BASE = ROOT
GENERATED = BASE / "generated"
CASES = {
    "quotes": {
        "request": "请从公开的 Quotes to Scrape 网站采集全部名言的引文和作者，并翻页直到没有下一页。",
        "host": "quotes.toscrape.com",
    },
    "books": {
        "request": "请从公开的 Books to Scrape 网站采集所有图书的完整书名和数值价格，翻页直到没有下一页。",
        "host": "books.toscrape.com",
    },
    "vague": {
        "request": "帮我采集一些图书信息。",
        "host": None,
    },
}


def plan_case(case, provider, key, model):
    request = CASES[case]["request"]
    plan = make_plan(request, "", provider, key, model)
    GENERATED.mkdir(exist_ok=True)
    path = GENERATED / f"real_plan_{case}.json"
    path.write_text(json.dumps({"request": request, "plan": plan,
                                "provider": provider, "model": model}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return {"plan_file": str(path), "plan": plan}


def run_case(case, provider, key, model):
    if case == "vague":
        raise ValueError("模糊需求应先回答澄清问题，不自动探索")
    path = GENERATED / f"real_plan_{case}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    plan = saved["plan"]
    host = urlparse(plan["start_url"]).hostname
    if plan["needs_clarification"] or host != CASES[case]["host"]:
        raise ValueError("计划尚需澄清，或模型选择的网站与案例目标不一致；请先检查计划")
    if saved["provider"] != provider or saved["model"] != model:
        raise ValueError("当前服务/模型与生成该计划时不同，请重新规划")
    result = explore(saved["request"], plan, provider, key, model, lambda msg: print(msg, flush=True))
    task_path = GENERATED / f"real_task_{case}.json"
    task_path.write_text(json.dumps(result["task"], ensure_ascii=False, indent=2), encoding="utf-8")
    (GENERATED / f"real_result_{case}.json").write_text(json.dumps({
        "samples": result["samples"], "mapping": result["mapping"],
        "next_xpath": result["next_xpath"], "trace": result["trace"]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return {"task_file": str(task_path), "samples": result["samples"],
            "mapping": result["mapping"], "next_xpath": result["next_xpath"],
            "trace": result["trace"]}


def revise_vague(provider, key, model):
    saved = json.loads((GENERATED / "real_plan_vague.json").read_text(encoding="utf-8"))
    answers = ["目标站点使用 https://books.toscrape.com/",
               "只采集完整书名和数值价格，不需要其他字段或详情页",
               "采集全站所有图书，翻页直到没有下一页"]
    history = [{"request": saved["request"], "url_hint": "", "answers": [],
                "plan": saved["plan"]}]
    plan = make_plan(saved["request"], "", provider, key, model, history, answers)
    path = GENERATED / "real_plan_vague_revised.json"
    path.write_text(json.dumps({"request": saved["request"], "answers": answers,
                                "plan": plan, "provider": provider, "model": model},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return {"plan_file": str(path), "plan": plan}


def execute_case(case):
    if case == "vague":
        raise ValueError("模糊需求应先回答澄清问题")
    task = json.loads((GENERATED / f"real_task_{case}.json").read_text(encoding="utf-8"))
    result_file = GENERATED / f"real_result_{case}.json"
    sample = (json.loads(result_file.read_text(encoding="utf-8"))["samples"][0]
              if result_file.exists() else
              {field["name"]: field["exampleValue"] for field in task["outputParameters"]})
    public_case = next(item for item in PUBLIC_CASES if item["name"] ==
                       ("Quotes to Scrape" if case == "quotes" else "Books to Scrape"))
    return execute_compiled_task(public_case, task, sample)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("plan", "run", "revise", "execute"))
    parser.add_argument("case", choices=CASES)
    args = parser.parse_args()
    provider, key, model = resolve_model_config({})
    sys.stdout.reconfigure(encoding="utf-8")
    if args.stage == "plan":
        result = plan_case(args.case, provider, key, model)
    elif args.stage == "run":
        result = run_case(args.case, provider, key, model)
    elif args.stage == "revise" and args.case == "vague":
        result = revise_vague(provider, key, model)
    elif args.stage == "execute":
        result = execute_case(args.case)
    else:
        parser.error("revise 仅支持 vague 案例")
    print(json.dumps(result, ensure_ascii=False, indent=2))
