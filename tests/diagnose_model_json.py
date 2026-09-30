"""Inspect completion metadata for the Taobao planning request without logging secrets."""
import json
import sys

import _bootstrap

import llm
from agent import make_plan
from settings import resolve_model_config


REQUEST = "帮我采集淘宝上羽毛球拍的相关信息，采集3页，需要按照销量进行排序，并获取这些信息：商品名称，价格，销量，店铺名称"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    provider, key, model = resolve_model_config({})
    original_post = llm._post

    def capture(url, payload, headers):
        response = original_post(url, payload, headers)
        choice = (response.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content") or ""
        print(json.dumps({"model": model, "finish_reason": choice.get("finish_reason"),
                          "content_length": len(content), "usage": response.get("usage")},
                         ensure_ascii=False), flush=True)
        return response

    llm._post = capture
    plan = make_plan(REQUEST, "", provider, key, model)
    print(json.dumps({"start_url": plan["start_url"], "pagination": plan["pagination"],
                      "page_limit": plan["page_limit"], "fields": plan["fields"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
