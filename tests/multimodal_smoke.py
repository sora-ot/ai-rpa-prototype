"""Read a canvas-only code through the actual screenshot adapter; optional API call."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import quote

import _bootstrap
from selenium import webdriver
from selenium.webdriver.chrome.service import Service

from agent import _compact
from llm import ask_json
from observer import observe
from settings import resolve_model_config


def run(real_model=False):
    root = Path(__file__).resolve().parent.parent
    bundled = root.parent / "EasySpider" / "ElectronJS" / "chrome_win64"
    temporary = root / "browser_tmp"
    temporary.mkdir(exist_ok=True)
    code = f"SCREEN{secrets.randbelow(1000000):06d}"
    html = '<html><head><title>Screenshot test</title></head><body><canvas id="image" width="850" height="200"></canvas></body></html>'
    with tempfile.TemporaryDirectory(prefix="multimodal-", dir=temporary) as profile:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=960,720")
        options.add_argument(f"--user-data-dir={profile}")
        binary = os.environ.get("AI_RPA_CHROME_BINARY")
        driver_path = os.environ.get("AI_RPA_CHROMEDRIVER")
        if os.name == "nt":
            binary = binary or str(bundled / "chrome.exe")
            driver_path = driver_path or str(bundled / "chromedriver_win64.exe")
        if binary:
            options.binary_location = binary
        driver = webdriver.Chrome(service=Service(driver_path) if driver_path else Service(), options=options)
        try:
            driver.get("data:text/html;charset=utf-8," + quote(html))
            driver.execute_script("""
                const ctx=document.getElementById('image').getContext('2d');
                ctx.fillStyle='#def0ff'; ctx.fillRect(0,0,850,200);
                ctx.fillStyle='#15284b'; ctx.font='bold 68px monospace';
                ctx.fillText(arguments[0],35,125);
            """, code)
            snapshot = observe(driver, with_screenshot=True)
            observation = _compact(snapshot)
            assert code not in json.dumps(observation), "test code leaked into text observation"
            png = base64.b64decode(snapshot["screenshot"], validate=True)
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            prompt = 'Read the SCREEN-prefixed code inside the image. Return only JSON with a "code" string.\n' + json.dumps(observation)
            if real_model:
                provider, key, model = resolve_model_config({"provider": "deepseek", "model": "deepseek-flash"})
                result = ask_json(provider, key, model, "Read the supplied screenshot accurately. Return JSON.", prompt, snapshot["screenshot"])
            else:
                def fake_api(_url, payload, _headers):
                    content = payload["messages"][1]["content"]
                    assert code not in content[0]["text"]
                    assert content[1]["image_url"]["url"] == "data:image/png;base64," + snapshot["screenshot"]
                    return {"choices": [{"message": {"content": json.dumps({"code": code})}}]}
                with patch("llm._post", side_effect=fake_api):
                    result = ask_json("deepseek", "test-key", "deepseek-flash", "Read the screenshot.", prompt, snapshot["screenshot"])
            assert result.get("code") == code, f"screenshot code mismatch: {result!r}"
            print(f"{'Real DeepSeek Flash' if real_model else 'Mock adapter'} screenshot test passed: canvas-only code read, PNG={len(png)} bytes")
        finally:
            driver.quit()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-model", action="store_true")
    run(parser.parse_args().real_model)
