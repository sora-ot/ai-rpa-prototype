"""Local only: inspect Taobao DOM structure with EasySpider's bundled Chrome."""
import json
import sys
import tempfile
import time
from pathlib import Path

import _bootstrap
from agent import _compact
from auth import detect_challenge, profile_path
from observer import observe


URL = "https://s.taobao.com/search?q=%E7%BE%BD%E6%AF%9B%E7%90%83%E6%8B%8D"
CHROME_DIR = _bootstrap.ROOT.parent / "EasySpider" / "ElectronJS" / "chrome_win64"


def main():
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    sys.stdout.reconfigure(encoding="utf-8")
    options = webdriver.ChromeOptions()
    options.binary_location = str(CHROME_DIR / "chrome.exe")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-gpu")
    if "--disable-gpu-sandbox" in sys.argv:
        options.add_argument("--disable-gpu-sandbox")
    temporary_root = _bootstrap.ROOT / "browser_tmp"
    temporary_root.mkdir(exist_ok=True)
    temporary_profile = (tempfile.TemporaryDirectory(dir=temporary_root)
                         if "--fresh-profile" in sys.argv else None)
    options.add_argument(f"--user-data-dir={temporary_profile.name if temporary_profile else profile_path(URL)}")
    options.add_argument("--profile-directory=Default")
    try:
        driver = webdriver.Chrome(service=Service(str(CHROME_DIR / "chromedriver_win64.exe")),
                                  options=options)
    except BaseException:
        if temporary_profile:
            temporary_profile.cleanup()
        raise
    try:
        driver.get(URL)
        time.sleep(4)
        if "--sales-details" in sys.argv or "--local-prepare" in sys.argv:
            from selenium.webdriver.common.by import By

            driver.find_element(By.XPATH,
                '//li[@role="tab" and normalize-space(.)="销量"]').click()
            time.sleep(4)
        snapshot = observe(driver)
        if "--local-prepare" in sys.argv:
            from selenium.webdriver.common.by import By
            from agent import _prepare_extract

            group = snapshot["lists"][0]
            first = group["first_xpath"]
            fields = []
            for name, prefix, attribute in (
                    ("商品名称", "title--", "text"),
                    ("价格", "innerPriceWrapper--", "number"),
                    ("销量", "realSales--", "text"),
                    ("店铺名称", "shopNameText--", "text")):
                matches = [element for element in snapshot["elements"]
                           if element["absolute_xpath"].startswith(first + "/")
                           and any(token.startswith(prefix) for token in
                                   element.get("class_name", "").split())]
                if not matches:
                    raise RuntimeError(f"第一条商品内未找到 {name} 的候选")
                fields.append({"name": name, "candidate_id": matches[0]["id"],
                               "attribute": attribute})
            decision = {"mode": "list", "list_id": group["id"], "fields": fields,
                        "next_id": snapshot["next_candidates"][0]["id"]}
            extraction, samples = _prepare_extract(snapshot, decision, driver)
            print(json.dumps({"base_count": len(driver.find_elements(By.XPATH, group["xpath"])),
                              "filtered_count": len(driver.find_elements(By.XPATH,
                                                                 extraction["list_xpath"])),
                              "list_xpath": extraction["list_xpath"],
                              "fields": [{"name": item["name"], "xpath": item["relative_xpath"]}
                                         for item in extraction["fields"]],
                              "samples": samples[:2]}, ensure_ascii=False, indent=2))
            return
        if "--sales-details" in sys.argv:
            from selenium.webdriver.common.by import By

            group = snapshot["lists"][0]
            cards = driver.find_elements(By.XPATH, group["xpath"])
            details = []
            for index, card in enumerate(cards[:12]):
                nodes = card.find_elements(By.XPATH,
                    './/*[contains(@class,"Sales") or contains(@class,"sales") or '
                    'contains(text(),"人付款") or contains(text(),"人收货")]')
                details.append({"index": index + 1, "values": [
                    {"tag": node.tag_name, "class": node.get_attribute("class"),
                     "text": node.text[:80]} for node in nodes if node.text.strip()][:20]})
            print(json.dumps({"count": len(cards), "details": details},
                             ensure_ascii=False, indent=2))
            return
        if "--list-gaps" in sys.argv:
            from selenium.webdriver.common.by import By

            group = snapshot["lists"][0]
            cards = driver.find_elements(By.XPATH, group["xpath"])
            details = [{"index": index + 1, "class": card.get_attribute("class"),
                        "text": card.text[:100],
                        "html_chars": len(card.get_attribute("outerHTML") or "")}
                       for index, card in enumerate(cards)
                       if not card.text.strip() or (index + 1) in (15, 30)]
            print(json.dumps({"list_xpath": group["xpath"], "count": len(cards),
                              "gaps": details}, ensure_ascii=False, indent=2))
            return
        if "--controls" in sys.argv:
            controls = [{"id": item["id"], "text": item.get("text", "")[:50],
                         "xpath": item["xpath"], "selected": item.get("selected")}
                        for item in snapshot["elements"]
                        if item.get("text", "").strip() in ("销量", "下一页")]
            print(json.dumps({"auth_challenge": detect_challenge(driver, URL),
                              "controls": controls,
                              "lists": [{"id": item["id"], "count": item["count"]}
                                        for item in snapshot["lists"][:5]]},
                             ensure_ascii=False, indent=2))
            return
        if "--fields" in sys.argv:
            first = snapshot["lists"][0]["first_xpath"]
            details = [{"id": item["id"], "tag": item["tag"],
                        "class": item.get("class_name", ""),
                        "text": item.get("text", "")[:100],
                        "relative_xpath": item["absolute_xpath"][len(first):]}
                       for item in snapshot["elements"]
                       if item["absolute_xpath"].startswith(first + "/")]
            controls = [{"id": item["id"], "text": item.get("text", ""),
                         "xpath": item["xpath"], "selected": item.get("selected")}
                        for item in snapshot["elements"]
                        if item.get("text", "").strip() in ("销量", "下一页")]
            print(json.dumps({"fields": details, "controls": controls},
                             ensure_ascii=False, indent=2))
            return
        ax_tree = driver.execute_cdp_cmd("Accessibility.getFullAXTree", {})
        html_length = driver.execute_script("return document.documentElement.outerHTML.length")
        summary = {"url": snapshot.get("url", ""),
                   "auth_challenge": detect_challenge(driver, URL),
                   "element_count": len(snapshot.get("elements", [])),
                   "next_count": len(snapshot.get("next_candidates", [])),
                   "next_labels": [item.get("text", "")[:30]
                                   for item in snapshot.get("next_candidates", [])],
                   "html_chars": html_length,
                   "accessibility_nodes": len(ax_tree.get("nodes", [])),
                   "accessibility_json_chars": len(json.dumps(ax_tree, ensure_ascii=False)),
                   "semantic_observation_chars": len(json.dumps(_compact(snapshot), ensure_ascii=False)),
                   "lists": []}
        for item in snapshot.get("lists", [])[:5]:
            markers = driver.execute_script("""
                const path=arguments[0],node=document.evaluate(path,document,null,
                  XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue;
                if(!node)return {found:false};
                const text=node.innerText||'';
                return {found:true,childCount:node.querySelectorAll('*').length,
                  product:/羽毛球拍/.test(text),price:/[¥￥]|价格/.test(text),
                  sales:/已售|销量|人付款|人收货/.test(text),
                  shop:/店铺|旗舰店|专卖店/.test(text)};
            """, item["first_xpath"])
            summary["lists"].append({"id": item["id"], "count": item["count"],
                                     "xpath": item["xpath"], "markers": markers,
                                     "first_field_candidates": sum(
                                         e["absolute_xpath"].startswith(item["first_xpath"] + "/")
                                         for e in snapshot.get("elements", []))})
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        try:
            driver.quit()
        finally:
            if temporary_profile:
                temporary_profile.cleanup()


if __name__ == "__main__":
    main()
