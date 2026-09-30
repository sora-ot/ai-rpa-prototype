"""Exercise setup -> chat revisions -> confirmation in a real local browser."""
import sys
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from _bootstrap import ROOT
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

import server


def main():
    tentative = {"task_name": "图书采集", "steps": ["选择图书网站", "提取字段"],
                 "fields": ["书名"], "questions": ["请说明目标网站和范围。"],
                 "needs_clarification": True, "start_url": "", "url_reason": "待补充",
                 "pagination": "none", "notes": "先澄清需求"}
    ready = {**tentative, "steps": ["打开公开图书网站", "提取完整书名和价格", "翻页到末页"],
             "fields": ["完整书名", "价格"], "questions": [], "needs_clarification": False,
             "start_url": "https://books.toscrape.com/", "url_reason": "公开示例站点",
             "pagination": "until_end", "notes": ""}
    preview = {"task": {"graph": []}, "samples": [{"完整书名": "A Light in the Attic", "价格": "51.77"}],
               "mapping": [{"name": "完整书名", "xpath": "//h3/a"}],
               "pagination": "until_end", "next_xpath": "//li[@class='next']/a",
               "profile_dir": "G:/research/easyspider/ai-rpa-prototype/profiles/test",
               "profile_relative_source": "../../ai-rpa-prototype/profiles/test"}
    web = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    chrome = ROOT.parent / "EasySpider" / "ElectronJS" / "chrome_win64"
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    if (chrome / "chrome.exe").is_file():
        options.binary_location = str(chrome / "chrome.exe")
    driver_path = chrome / "chromedriver_win64.exe"
    service = Service(str(driver_path)) if driver_path.is_file() else Service()
    try:
        with patch("server.make_plan", side_effect=[tentative, ready]) as planner, \
                patch("server.explore", return_value=preview) as explorer, \
                webdriver.Chrome(service=service, options=options) as browser:
            browser.get(f"http://127.0.0.1:{web.server_port}/")
            wait = WebDriverWait(browser, 10)
            assert browser.find_element(By.ID, "setup-card").is_displayed()
            assert not browser.find_element(By.ID, "chat-card").is_displayed()
            browser.find_element(By.ID, "api-key").send_keys("test-key")
            browser.find_element(By.ID, "enter-chat").click()
            wait.until(lambda d: d.find_element(By.ID, "chat-card").is_displayed())
            assert not browser.find_element(By.ID, "setup-card").is_displayed()
            chat = browser.find_element(By.ID, "chat-input")
            assert chat.get_attribute("maxlength") == "1000"
            assert "0/15" in browser.find_element(By.ID, "turn-hint").text
            chat.send_keys("帮我采集一些图书信息")
            browser.find_element(By.ID, "send").click()
            wait.until(lambda d: "已规划 1/" in d.find_element(By.ID, "turn-hint").text)
            assert "请说明目标网站和范围" in browser.find_element(By.ID, "chat-log").text
            assert not browser.find_element(By.ID, "run").is_enabled()
            explorer.assert_not_called()
            chat.send_keys("使用 Books to Scrape；采集完整书名和价格；翻页到末页")
            browser.find_element(By.ID, "send").click()
            wait.until(lambda d: "已规划 2/" in d.find_element(By.ID, "turn-hint").text)
            assert browser.find_element(By.ID, "run").is_enabled()
            assert len(browser.find_elements(By.CSS_SELECTOR, ".bubble")) == 4
            assert planner.call_args_list[1].args[5][0]["request"] == "帮我采集一些图书信息"
            explorer.assert_not_called()
            browser.find_element(By.ID, "run").click()
            wait.until(lambda d: d.find_element(By.ID, "result-card").is_displayed())
            assert "A Light in the Attic" in browser.find_element(By.ID, "samples").text
            browser.execute_script("showRunCommand('G:/tmp/generated/example.json')")
            assert 'python run_native.py --task-file "generated/example.json"' == (
                browser.find_element(By.ID, "run-command").get_attribute("textContent"))
            profile_hint = browser.find_element(By.ID, "profile-path").get_attribute("textContent")
            assert "../../ai-rpa-prototype/profiles/test" in profile_hint
            assert "本地直接执行（带用户信息模式）" in profile_hint
            explorer.assert_called_once()
            print("UI smoke passed: setup, two chat turns, clarification gate, confirmation, preview")
    finally:
        web.shutdown()
        web.server_close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
