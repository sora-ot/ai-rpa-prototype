"""Manual Taobao exploration diagnostic using the configured model and profile."""
import json
import sys

import _bootstrap

import agent
import llm
from auth import LoginGate
from settings import resolve_model_config


REQUEST = "帮我采集淘宝上羽毛球拍的相关信息，采集3页，需要按照销量进行排序，并获取这些信息：商品名称，价格，销量，店铺名称"
PLAN = {
    "task_name": "淘宝羽毛球拍",
    "start_url": "https://s.taobao.com/search?q=%E7%BE%BD%E6%AF%9B%E7%90%83%E6%8B%8D",
    "steps": ["搜索并采集商品列表"],
    "fields": ["商品名称", "价格", "销量", "店铺名称"],
    "pagination": "page_limit",
    "page_limit": 3,
    "requires_login": True,
    "requires_detail": False,
    "keywords": ["羽毛球拍"],
    "output_format": "csv",
    "deduplicate": False,
}


def run():
    provider, key, model = resolve_model_config({})
    original = agent.ask_json
    original_post = llm._post

    def capture(url, payload, headers):
        response = original_post(url, payload, headers)
        choice = (response.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content") or ""
        print("MODEL " + json.dumps({"finish_reason": choice.get("finish_reason"),
                                      "content_length": len(content),
                                      "usage": response.get("usage")}, ensure_ascii=False), flush=True)
        return response

    llm._post = capture

    def ask(*args, **kwargs):
        user = json.loads(args[4]) if len(args) > 4 else {}
        if args[3] is agent.ACT_SYSTEM:
            observation = user.get("observation", {})
            print("OBS " + json.dumps({"url": observation.get("url"),
                                       "elements": len(observation.get("elements", [])),
                                       "lists": [(item.get("id"), item.get("count"),
                                                  item.get("samples", [""])[0][:80])
                                                 for item in observation.get("lists", [])],
                                       "next": user.get("available_candidate_ids", {}).get("next", [])},
                                      ensure_ascii=False), flush=True)
        result = original(*args, **kwargs)
        if args[3] is agent.ACT_SYSTEM:
            print("DECISION " + json.dumps(result, ensure_ascii=False), flush=True)
        return result

    agent.ask_json = ask
    gate = LoginGate(
        PLAN["start_url"],
        lambda reason: print("WAIT_LOGIN " + reason, flush=True),
        lambda: print("LOGIN_OK", flush=True),
        wait_seconds=15,
    )
    result = agent.explore(
        REQUEST, PLAN, provider, key, model,
        lambda message: print("EVENT " + message, flush=True),
        max_actions=30, login_gate=gate,
    )
    print(json.dumps({
        "samples": result["samples"], "mapping": result["mapping"],
        "pagination": result["pagination"], "page_limit": result["page_limit"],
        "next_xpath": result["next_xpath"], "trace": result["trace"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
