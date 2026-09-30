"""Deterministic local pages for complex extraction and manual-login tests."""
import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_html(self, body, status=200, headers=()):
        raw = ('<!doctype html><html><head><meta charset="utf-8"></head><body>' +
               body + '</body></html>').encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, path):
        self.send_response(302)
        self.send_header('Location', path)
        self.end_headers()

    def authenticated(self):
        cookie = self.headers.get('Cookie', '')
        return any(part.strip().split('=', 1)[-1] in self.server.sessions
                   for part in cookie.split(';') if part.strip().startswith('demo_session='))

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != '/login':
            return self.send_error(404)
        length = int(self.headers.get('Content-Length', 0))
        form = parse_qs(self.rfile.read(length).decode('utf-8'))
        if form.get('username') != ['demo'] or form.get('password') != ['local-only']:
            return self.send_html('<p>Login failed</p>', 403)
        token = secrets.token_urlsafe(20)
        self.server.sessions.add(token)
        self.send_response(302)
        self.send_header('Set-Cookie', f'demo_session={token}; HttpOnly; SameSite=Lax; Max-Age=3600; Path=/')
        self.send_header('Location', parse_qs(parsed.query).get('next', ['/orders?page=1'])[0])
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        page = int(parse_qs(parsed.query).get('page', ['1'])[0])
        path = parsed.path
        if path == '/login':
            next_page = parse_qs(parsed.query).get('next', ['/orders?page=1'])[0]
            return self.send_html(f'<h1>Login</h1><form method="post" action="/login?next={next_page}">'
                '<input name="username" autocomplete="username">'
                '<input name="password" type="password" value="local-only" autocomplete="current-password">'
                '<button type="submit">Sign in</button></form>')
        if path == '/catalog' and page in (1, 2, 3):
            cards = ''.join(f'<article class="product"><h2>Item {n}</h2>'
                f'<span class="price">{n * 10}.00</span>'
                f'<a class="details" href="/detail/{n}">Details</a></article>'
                for n in range((page-1)*2+1, page*2+1))
            nxt = f'<li class="next"><a href="/catalog?page={page+1}">Next</a></li>' if page < 3 else ''
            return self.send_html(f'<main>{cards}<ul>{nxt}</ul></main>')
        if path.startswith('/detail/'):
            n = int(path.split('/')[-1])
            if n not in range(1, 7):
                return self.send_error(404)
            if self.server.detail_requires_login and not self.authenticated():
                return self.redirect('/login?next=' + self.path)
            return self.send_html(f'<main><span class="stock">Stock {n}</span>'
                                  f'<code class="sku">SKU-{n}</code></main>')
        if path == '/news' and page in (1, 2):
            indices = (1, 2, 3) if page == 1 else (4, 5)
            cards = ''.join(f'<article class="news"><h2>News {n}</h2>'
                + (f'<time>2026-09-{n:02d}</time>' if n != 4 else '')
                + f'<a class="details" target="_blank" href="/story/{n}">Read</a></article>'
                for n in indices)
            nxt = f'<li class="next"><a href="/news?page=2">Next</a></li>' if page == 1 else ''
            return self.send_html(f'<main>{cards}<ul>{nxt}</ul></main>')
        if path.startswith('/story/'):
            n = int(path.split('/')[-1])
            if n not in range(1, 6):
                return self.send_error(404)
            return self.send_html(f'<main><p class="body">Full body for News {n}</p></main>')
        if path == '/search':
            return self.send_html('<main><form action="/results" method="get">'
                '<input id="query" name="q" placeholder="Keyword">'
                '<select id="category" name="category"><option>All</option>'
                '<option>Research</option></select>'
                '<button id="search-button" type="submit">Search</button></form></main>')
        if path == '/results' and page in (1, 2):
            query = parse_qs(parsed.query)
            keyword = query.get('q', [''])[0]
            if keyword not in ('A', 'B') or query.get('category') != ['Research']:
                return self.send_error(400)
            names = ['Common', f'{keyword}-1'] if page == 1 else [f'{keyword}-2', f'{keyword}-3']
            cards = ''.join(f'<article class="result"><h2>{name}</h2>'
                f'<a href="/item/{name}">Open</a></article>' for name in names)
            nxt = (f'<li class="next"><a href="/results?q={keyword}&category=Research&page=2">Next</a></li>'
                   if page == 1 else '')
            return self.send_html(f'<main>{cards}<ul>{nxt}</ul></main>')
        if path == '/comments':
            return self.send_html('''<main><section id="feed">
                <article class="comment"><span class="score">5</span><p>Comment 1</p><time>2026-09-01</time></article>
                <article class="comment"><span class="score">4</span><p>Comment 2</p><time>2026-09-02</time></article>
                </section><article class="ad">Advertisement</article><div style="height:1100px"></div></main>
                <script>let loaded=false;window.addEventListener('scroll',()=>{if(loaded)return;loaded=true;
                  setTimeout(()=>{for(let n=3;n<=4;n++){const x=document.createElement('article');x.className='comment';
                    x.innerHTML='<span class="score">'+(7-n)+'</span><p>Comment '+n+'</p><time>2026-09-0'+n+'</time>';
                    document.getElementById('feed').append(x);}},250);});</script>''')
        if path.startswith('/item/'):
            name = path.split('/')[-1]
            if name in self.server.always_fail_items:
                return self.send_html('<main><h1>Temporary failure</h1></main>', 503)
            if name in self.server.fail_once_items and name not in self.server.failed_items:
                self.server.failed_items.add(name)
                return self.send_html('<main><h1>Temporary failure</h1></main>', 503)
            return self.send_html(f'<main><h1>Product {name}</h1>'
                                  f'<span id="spec">Spec {name}</span></main>')
        if path in ('/orders', '/order') or path.startswith('/order/'):
            if path == '/orders' and page == 2 and self.server.expire_orders_page2 and not self.server.expired_orders:
                self.server.sessions.clear()
                self.server.expired_orders = True
            if not self.authenticated():
                return self.redirect('/login?next=' + self.path)
            if path == '/orders' and page in (1, 2):
                cards = ''.join(f'<article class="order"><h2>Order {n}</h2>'
                    f'<a class="details" href="/order/{n}">Details</a></article>'
                    for n in range((page-1)*3+1, page*3+1))
                nxt = f'<li class="next"><a href="/orders?page=2">Next</a></li>' if page == 1 else ''
                return self.send_html(f'<main>{cards}<ul>{nxt}</ul></main>')
            if path.startswith('/order/'):
                n = int(path.split('/')[-1])
                return self.send_html(f'<main><span class="date">2026-09-{n:02d}</span>'
                                      f'<span class="amount">{n * 7}.00</span></main>')
        self.send_error(404)


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        import sys
        if sys.exc_info()[0] not in (ConnectionResetError, ConnectionAbortedError):
            super().handle_error(request, client_address)


@contextmanager
def fixture_server():
    server = QuietServer(('127.0.0.1', 0), FixtureHandler)
    server.sessions = set()
    server.fail_once_items = set()
    server.always_fail_items = set()
    server.failed_items = set()
    server.expire_orders_page2 = False
    server.detail_requires_login = False
    server.expired_orders = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
