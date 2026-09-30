"""Optional real-browser checks on public, login-free sites using stub decisions."""
import json
import sys
from unittest.mock import patch

import _bootstrap
from agent import explore
from public_cases import CASES


def select_for_site(site, _provider, _key, _model, _system, user, _screenshot=None):
    observation = json.loads(user)["observation"]
    elements = observation["elements"]
    if site == "Quotes to Scrape":
        group = next(g for g in observation["lists"] if g["count"] == 10 and "Einstein" in g["samples"][0])
        first = group["first_xpath"] + "/"
        quote = next(e for e in elements if e["absolute_xpath"].startswith(first)
                     and e["tag"] == "span" and "The world as we" in e["text"])
        author = next(e for e in elements if e["absolute_xpath"].startswith(first)
                      and e["tag"] == "small" and "Einstein" in e["text"])
        fields = [{"name": "quote", "candidate_id": quote["id"], "attribute": "text"},
                  {"name": "author", "candidate_id": author["id"], "attribute": "text"}]
    else:
        group = next(g for g in observation["lists"] if g["count"] == 20 and "Light in the" in g["samples"][0])
        first = group["first_xpath"] + "/"
        title_matches = [e for e in elements if e["absolute_xpath"].startswith(first)
                         and e["tag"] == "a" and "Light in the" in e["text"]]
        if not title_matches:
            raise RuntimeError("Books first item candidates: " + repr((first, len(elements),
                [(e["tag"], e["text"], e["absolute_xpath"]) for e in elements
                 if e["absolute_xpath"].startswith(first)][:25],
                 [(e["tag"], e["text"], e["absolute_xpath"]) for e in elements[-5:]])))
        title = title_matches[0]
        price = next(e for e in elements if e["absolute_xpath"].startswith(first)
                     and e["tag"] == "p" and "51.77" in e["text"])
        fields = [{"name": "title", "candidate_id": title["id"], "attribute": "title"},
                  {"name": "price", "candidate_id": price["id"], "attribute": "number"}]
    next_link = next(e for e in elements if e["tag"] == "a" and e["href"]
                     and ("page/2/" in e["href"] or "page-2.html" in e["href"]))
    return {"action": "extract", "mode": "list", "list_id": group["id"],
            "fields": fields, "next_id": next_link["id"]}


def main():
    results = []
    for case in CASES:
        def fake_model(*args):
            return select_for_site(case["name"], *args)
        with patch("agent.ask_json", side_effect=fake_model):
            result = explore(case["name"], {"start_url": case["url"],
                        "task_name": case["name"], "pagination": "until_end"},
                        "deepseek", "stub", "", lambda _message: None)
        assert len(result["samples"]) >= case["minimum_items"] // 4
        assert all(all(value for value in row.values()) for row in result["samples"])
        assert result["pagination"] == "until_end"
        assert " next " in result["next_xpath"], result["next_xpath"]
        if case["name"] == "Books to Scrape":
            assert result["samples"][0]["title"] == "A Light in the Attic"
            assert result["samples"][0]["price"] == "51.77"
        results.append({"site": case["name"], "sample_count": len(result["samples"]),
                        "sample": result["samples"][0], "mapping": result["mapping"],
                        "next_xpath": result["next_xpath"]})
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
