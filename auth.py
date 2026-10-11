"""Local, user-controlled authentication handoff for browser exploration."""
import hashlib
import re
import threading
from pathlib import Path
from urllib.parse import urlparse


PROFILE_ROOT = Path(__file__).resolve().parent / "profiles"
_PROFILE_LOCK = threading.Lock()
_ACTIVE_PROFILES = set()
LOGIN_WAIT_SECONDS = 900


AUTH_OVERLAY_JS = r"""
const shown = e => {
  const s = getComputedStyle(e), r = e.getBoundingClientRect();
  return s.display !== 'none' && s.visibility !== 'hidden' &&
    Number(s.opacity || 1) > 0 && r.width > 0 && r.height > 0 &&
    (!e.checkVisibility || e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}));
};
const meta = e => {
  const cls = typeof e.className === 'string' ? e.className : '';
  return ((e.id || '') + ' ' + cls + ' ' + (e.getAttribute('role') || '') + ' ' +
    (e.getAttribute('aria-label') || '')).toLowerCase();
};
const viewport = Math.max(1, innerWidth * innerHeight);
const authName = /login|signin|sign-in|auth|passport|account|captcha|verify|security|middleware[ _-]?frame[ _-]?widget/;
return [...document.querySelectorAll('div,section,aside,form,iframe,[role="dialog"]')]
  .some(e => {
    if (!shown(e)) return false;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e), m = meta(e);
    const area = r.width * r.height;
    const fixed = s.position === 'fixed' || s.position === 'absolute';
    const full = area >= viewport * 0.35 && r.width >= innerWidth * 0.55 &&
      r.height >= innerHeight * 0.35;
    const middleware = /j_middleware_frame_widget/.test(m);
    return middleware || (authName.test(m) && fixed && full);
  });
"""


def profile_path(url):
    site = urlparse(url).netloc.lower()
    if not site:
        raise ValueError("登录资料目录需要有效的网址")
    label = re.sub(r"[^a-z0-9]+", "_", site).strip("_")[:35] or "site"
    digest = hashlib.sha256(site.encode("utf-8")).hexdigest()[:12]
    return PROFILE_ROOT / f"{label}_{digest}"


def detect_challenge(driver, start_url):
    """Return a short reason without reading form values or page content."""
    try:
        if driver.execute_script(AUTH_OVERLAY_JS):
            return "页面出现登录或验证遮罩层"
    except Exception:
        # Older drivers or test doubles may not support this extra probe. The
        # existing password/captcha checks remain the fallback.
        pass
    state = driver.execute_script("""
      const shown = e => { const s=getComputedStyle(e),r=e.getBoundingClientRect();
        return s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||1)>0 && r.width>0 && r.height>0 &&
          (!e.checkVisibility || e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})); };
      return {url:location.href,title:document.title,
        password:[...document.querySelectorAll('input[type=password]')].some(shown),
        captcha:[...document.querySelectorAll('iframe,[id],[class]')]
          .some(e => shown(e) && /captcha|recaptcha|hcaptcha|turnstile/i.test(
            (e.id||'')+' '+(typeof e.className==='string'?e.className:'')+' '+(e.getAttribute('src')||'')))};
    """)
    current = urlparse(state["url"])
    start = urlparse(start_url)
    path = current.path.lower()
    if state["password"]:
        return "页面出现登录密码框"
    if state["captcha"] or "captcha" in path or any(
            word in state["title"].lower() for word in ("验证码", "安全验证", "captcha")):
        return "页面出现人工验证"
    if re.search(r"/(login|signin|sign-in|auth)(/|$)", path):
        return "页面跳转到登录页"
    if current.hostname != start.hostname and re.search(r"/(login|signin|sign-in|auth|authorize)(/|$)", path):
        return "页面跳转到第三方登录站"
    return ""


class LoginGate:
    def __init__(self, start_url, on_wait, on_resume, wait_seconds=LOGIN_WAIT_SECONDS):
        self.profile_dir = profile_path(start_url)
        self.on_wait = on_wait
        self.on_resume = on_resume
        self.wait_seconds = wait_seconds
        self.event = threading.Event()
        self.waiting = False
        self.cancelled = False
        self.used = False

    def acquire(self):
        with _PROFILE_LOCK:
            if self.profile_dir in _ACTIVE_PROFILES:
                raise RuntimeError("这个站点的登录浏览器已在使用，请等当前探索结束")
            _ACTIVE_PROFILES.add(self.profile_dir)
        try:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.release()
            raise

    def release(self):
        with _PROFILE_LOCK:
            _ACTIVE_PROFILES.discard(self.profile_dir)

    def complete(self):
        if not self.waiting:
            raise ValueError("当前没有等待登录的浏览器")
        self.event.set()

    def cancel(self):
        self.cancelled = True
        self.event.set()

    def wait_for_login(self, driver, start_url, reason):
        self.used = True
        for _ in range(3):
            self.waiting = True
            self.event.clear()
            self.on_wait(reason)
            if not self.event.wait(self.wait_seconds):
                self.waiting = False
                raise TimeoutError("等待人工登录超时，请重新探索")
            self.waiting = False
            if self.cancelled:
                raise RuntimeError("用户取消了人工登录")
            driver.get(start_url)
            reason = detect_challenge(driver, start_url)
            if not reason:
                self.on_resume()
                return
        raise RuntimeError("登录后仍无法访问目标页；请检查账号或站点验证")
