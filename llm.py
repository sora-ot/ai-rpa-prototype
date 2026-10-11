"""Small HTTP adapters for the three requested model APIs."""
import json
import re
import urllib.error
import urllib.request


DEFAULT_MODELS = {
    "deepseek": "deepseek-flash",
    "gpt": "gpt-4o-mini",
    "gemini": "gemini-2.5-flash",
}


def _decode_object(output):
    """Accept a JSON object, including a single fenced object from a model."""
    if not isinstance(output, str) or not output.strip():
        raise ValueError("模型返回了空内容")
    content = output.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", content, re.I)
    if fenced:
        content = fenced.group(1).strip()
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("模型返回的内容不是完整 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("模型返回的 JSON 必须是对象")
    return value


def _post(url, payload, headers):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=75) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(1500).decode("utf-8", "replace")
        raise RuntimeError(f"模型 API 返回 HTTP {exc.code}: {detail}") from exc


def ask_json(provider, api_key, model, system, user, screenshot_b64=None):
    if provider not in DEFAULT_MODELS:
        raise ValueError("仅支持 deepseek、gpt、gemini")
    if not api_key:
        raise ValueError("请提供模型 API Key")
    model = model or DEFAULT_MODELS[provider]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
        raise ValueError("模型名仅可包含字母、数字、点、下划线和连字符")
    last_error = None
    for attempt in range(2):
        retry_note = ("\n\n上次响应无法解析。请只输出一个完整且简短的 JSON 对象，"
                      "不要使用 Markdown，不要附加解释。" if attempt else "")
        # Reasoning models count hidden reasoning against the completion budget.
        # A short visible JSON object can therefore still end with `length`.
        output_budget = (8192 if attempt == 0 else 16384) if provider == "deepseek" else (
            4096 if attempt == 0 else 8192)
        if provider == "gemini":
            parts = [{"text": system + "\n\n" + user + retry_note}]
            if screenshot_b64:
                parts.append({"inline_data": {"mime_type": "image/png", "data": screenshot_b64}})
            result = _post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                {"contents": [{"role": "user", "parts": parts}],
                 "generationConfig": {"responseMimeType": "application/json",
                                      "temperature": 0.1, "maxOutputTokens": output_budget}},
                {"Content-Type": "application/json", "x-goog-api-key": api_key},
            )
            candidates = result.get("candidates") or []
            item = candidates[0] if candidates else {}
            output = "".join(p.get("text", "") for p in item.get("content", {}).get("parts", []))
            finish_reason = item.get("finishReason", "")
        else:
            content = [{"type": "text", "text": user + retry_note}]
            if screenshot_b64:
                content.append({"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + screenshot_b64, "detail": "low"}})
            output_limit = ({"max_tokens": output_budget} if provider == "deepseek"
                            else {"max_completion_tokens": output_budget})
            result = _post(
                "https://api.deepseek.com/chat/completions" if provider == "deepseek"
                else "https://api.openai.com/v1/chat/completions",
                {"model": model, "temperature": 0.1, **output_limit,
                 "response_format": {"type": "json_object"},
                 "messages": [{"role": "system", "content": system},
                              {"role": "user", "content": content if screenshot_b64
                               else user + retry_note}]},
                {"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
            )
            choices = result.get("choices") or []
            item = choices[0] if choices else {}
            output = item.get("message", {}).get("content")
            finish_reason = item.get("finish_reason", "")
        try:
            if str(finish_reason).lower() in ("length", "max_tokens"):
                raise ValueError("模型输出达到长度限制，JSON 被截断")
            return _decode_object(output)
        except ValueError as exc:
            last_error = exc
    raise ValueError(f"模型连续两次未返回合法 JSON：{last_error}。请重试或缩短需求。") from last_error
