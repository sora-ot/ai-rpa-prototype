"""Optional two-turn HTTP chat check using the configured model API."""
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import _bootstrap
import server
from settings import resolve_model_config


def main():
    provider, key, model = resolve_model_config({})
    if not key:
        raise RuntimeError("请先在 .env 配置模型 API Key")
    web = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{web.server_port}"

    def post(message, session=None):
        body = {"message": message, "provider": provider, "model": model}
        if session:
            body["session"] = session
        request = Request(base + "/api/plan", json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          {"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=95) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = json.load(exc).get("error", str(exc))
            raise RuntimeError(str(detail).replace(key, "[redacted]")) from exc

    try:
        first = post("帮我采集一些图书信息")
        second = post("使用 Books to Scrape；仅采集完整书名和数值价格；翻页直到没有下一页。",
                      first["session"])
        assert second["session"] == first["session"]
        assert second["revision"] == 2
        assert [item["role"] for item in second["messages"]] == [
            "user", "assistant", "user", "assistant"]
        assert second["plan"]["start_url"].startswith("https://books.toscrape.com/")
        assert not second["plan"]["needs_clarification"]
        print(json.dumps({"provider": provider, "model": model,
                          "first_questions": first["plan"]["questions"],
                          "revision": second["revision"],
                          "start_url": second["plan"]["start_url"],
                          "fields": second["plan"]["fields"],
                          "pagination": second["plan"]["pagination"]},
                         ensure_ascii=False, indent=2))
    finally:
        web.shutdown()
        web.server_close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
