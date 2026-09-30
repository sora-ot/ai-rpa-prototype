"""Read-only public-site selector checks; no model key or browser is required."""
import json
import re
import sys
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import _bootstrap
from lxml import html

from workflow import compile_task, validate_task


CASES = [
    {
        "name": "Quotes to Scrape",
        "url": "https://quotes.toscrape.com/",
        "second_url": "https://quotes.toscrape.com/page/2/",
        "last_url": "https://quotes.toscrape.com/page/10/",
        "list_xpath": '//div[contains(concat(" ", normalize-space(@class), " "), " quote ")]',
        "next_xpath": '//li[contains(concat(" ", normalize-space(@class), " "), " next ")]/a',
        "fields": [
            ("quote", "/span[1]", "text"),
            ("author", '/span[2]/small[contains(@class,"author")]', "text"),
        ],
        "minimum_items": 10,
    },
    {
        "name": "Books to Scrape",
        "url": "https://books.toscrape.com/",
        "second_url": "https://books.toscrape.com/catalogue/page-2.html",
        "last_url": "https://books.toscrape.com/catalogue/page-50.html",
        "list_xpath": '//article[contains(concat(" ", normalize-space(@class), " "), " product_pod ")]',
        "next_xpath": '//li[contains(concat(" ", normalize-space(@class), " "), " next ")]/a',
        "fields": [
            ("title", "/h3/a", "title"),
            ("price", '/div[2]/p[contains(@class,"price_color")]', "number"),
        ],
        "minimum_items": 20,
    },
]


def fetch(url):
    request = Request(url, headers={"User-Agent": "EasySpider-AI-Prototype-Selector-Check/1.0"})
    with urlopen(request, timeout=20) as response:
        return html.fromstring(response.read())


def check(case):
    first = fetch(case["url"])
    rows = first.xpath(case["list_xpath"])
    assert len(rows) >= case["minimum_items"], f"{case['name']}: first-page list was not found"
    samples = []
    for row in rows[:2]:
        sample = {}
        for name, xpath, attribute in case["fields"]:
            elements = row.xpath("." + xpath)
            assert elements, f"{case['name']}: {name} locator failed"
            if attribute == "title":
                sample[name] = (elements[0].get("title") or "").strip()
            elif attribute == "number":
                sample[name] = re.sub(r"[^0-9.]", "", elements[0].text_content())
            else:
                sample[name] = elements[0].text_content().strip()
            assert sample[name], f"{case['name']}: {name} is empty"
        samples.append(sample)
    next_links = first.xpath(case["next_xpath"])
    assert len(next_links) == 1, f"{case['name']}: next link was not found uniquely"
    actual_second = urljoin(case["url"], next_links[0].get("href"))
    assert actual_second == case["second_url"], (case["name"], actual_second)
    second = fetch(actual_second)
    assert len(second.xpath(case["list_xpath"])) >= case["minimum_items"]
    last = fetch(case["last_url"])
    assert not last.xpath(case["next_xpath"]), f"{case['name']}: last page still has a next link"
    fields = [
        {"name": name, "attribute": attribute, "xpath": case["list_xpath"] + xpath,
         "relative_xpath": xpath, "sample": samples[0][name]}
        for name, xpath, attribute in case["fields"]
    ]
    task = compile_task(case["url"], case["name"], [],
                        {"mode": "list", "list_xpath": case["list_xpath"],
                         "fields": fields, "next_xpath": case["next_xpath"]})
    assert validate_task(task)
    return {"site": case["name"], "first_page_items": len(rows), "samples": samples,
            "second_page": actual_second, "last_page_stops": True,
            "node_options": [node["option"] for node in task["graph"]]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps([check(case) for case in CASES], ensure_ascii=False, indent=2))
