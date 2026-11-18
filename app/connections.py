"""connections: every service, api or mcp, tool permission allow / ask / off, no secrets in files"""
import importlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

DATA_DIR = None   # set by server.py
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CLAUDE_CFG = os.path.expanduser('~/.claude.json')
PROTOCOL = '2025-06-18'


def conn_dir():
    d = os.path.join(DATA_DIR, 'connections')
    os.makedirs(d, exist_ok=True)
    return d


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'connection'


def load(cid):
    with open(os.path.join(conn_dir(), cid + '.json'), encoding='utf-8') as f:
        return json.load(f)


def save(c):
    with open(os.path.join(conn_dir(), c['id'] + '.json'), 'w', encoding='utf-8') as f:
        json.dump(c, f, indent=2, ensure_ascii=False)


def secret(cid):
    """key kept in secrets.json, never the connection file"""
    try:
        with open(os.path.join(DATA_DIR, 'secrets.json'), encoding='utf-8') as f:
            return json.load(f).get('connection:' + cid, '')
    except Exception:
        return ''


def set_secret(cid, key):
    p = os.path.join(DATA_DIR, 'secrets.json')
    try:
        with open(p, encoding='utf-8') as f:
            sec = json.load(f)
    except Exception:
        sec = {}
    sec['connection:' + cid] = (key or '').strip()
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(sec, f, indent=2)


def all_connections():
    out = []
    for n in sorted(os.listdir(conn_dir())):
        if n.endswith('.json'):
            try:
                with open(os.path.join(conn_dir(), n), encoding='utf-8') as f:
                    out.append(json.load(f))
            except Exception:
                pass
    return out


READ_WORDS = re.compile(r'(^|[-_.])(get|list|search|read|fetch|find|query|describe|view|show|count|check|lookup|browse|snapshot|info|status)([-_.]|$)', re.I)


def tool_kind(name, ann=None):
    """server hint first, else tool name"""
    ann = ann or {}
    if ann.get('readOnlyHint') is True:
        return 'read'
    if ann.get('destructiveHint') is True or ann.get('readOnlyHint') is False:
        return 'write'
    return 'read' if READ_WORDS.search(name) else 'write'


def summary(c):
    tools = c.get('tools', {})
    return {'id': c['id'], 'name': c['name'], 'via': c['via'], 'description': c.get('description', ''),
            'enabled': c.get('enabled', True), 'status': c.get('status', 'not checked'),
            'error': c.get('error', ''), 'checked': c.get('checked', ''), 'source': c.get('source', ''),
            'needs_key': c.get('needs_key', ''),
            'tools_total': len(tools), 'tools_on': sum(1 for t in tools.values() if t.get('permission') != 'off')}


# ---------- built-in api connectors ----------

def builtin_modules():
    d = os.path.join(APP_DIR, 'connectors')
    out = {}
    if not os.path.isdir(d):
        return out
    for n in sorted(os.listdir(d)):
        if n.endswith('.py') and not n.startswith('_'):
            try:
                m = importlib.import_module('connectors.' + n[:-3])
                out[m.MANIFEST['id']] = m
            except Exception:
                pass
    return out


def ensure_builtins():
    """file made first time only, later starts keep your choices"""
    have = {c['id'] for c in all_connections()}
    for cid, m in builtin_modules().items():
        if cid not in have:
            man = m.MANIFEST
            save({'id': cid, 'name': man['name'], 'via': 'api', 'description': man.get('description', ''),
                  'enabled': True, 'source': 'built in', 'needs_key': man.get('needs_key', ''),
                  'tools': {t['name']: {'description': t.get('description', ''), 'input': t.get('input', {}),
                                        'permission': t.get('default', 'allow')} for t in man['tools']}})


# ---------- mcp client: just enough to list and call tools ----------

class StdioMCP:
    def __init__(self, spec):
        env = dict(os.environ)
        env.update(spec.get('env') or {})
        self.p = subprocess.Popen([spec['command']] + list(spec.get('args') or []), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                  text=True, encoding='utf-8', bufsize=1,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.n = 0

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj) + '\n')
        self.p.stdin.flush()

    def request(self, method, params=None, timeout=40):
        self.n += 1
        rid = self.n
        self._send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params or {}})
        box = {}

        def read():
            for line in self.p.stdout:
                line = line.strip()
                if not line.startswith('{'):
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get('id') == rid:
                    box['msg'] = msg
                    return
        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(timeout)
        if 'msg' not in box:
            raise TimeoutError('the server did not answer %s in %ds' % (method, timeout))
        if 'error' in box['msg']:
            raise RuntimeError(box['msg']['error'].get('message', 'error'))
        return box['msg'].get('result', {})

    def notify(self, method):
        self._send({'jsonrpc': '2.0', 'method': method})

    def close(self):
        try:
            self.p.kill()
        except Exception:
            pass


class HttpMCP:
    def __init__(self, spec):
        self.url = spec['url']
        self.headers = dict(spec.get('headers') or {})
        self.session = None
        self.n = 0

    def _post(self, obj, timeout=40):
        h = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
             'MCP-Protocol-Version': PROTOCOL}
        h.update(self.headers)
        if self.session:
            h['Mcp-Session-Id'] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(obj).encode('utf-8'), headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            self.session = r.headers.get('Mcp-Session-Id') or self.session
            body = r.read().decode('utf-8', 'replace')
            ctype = r.headers.get('Content-Type', '')
        if not body.strip():
            return None
        if 'event-stream' in ctype:
            for line in body.splitlines():
                if line.startswith('data:'):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if msg.get('id') == obj.get('id'):
                        return msg
            return None
        return json.loads(body)

    def request(self, method, params=None, timeout=40):
        self.n += 1
        msg = self._post({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params or {}}, timeout)
        if not msg:
            raise RuntimeError('no answer to ' + method)
        if 'error' in msg:
            raise RuntimeError(msg['error'].get('message', 'error'))
        return msg.get('result', {})

    def notify(self, method):
        try:
            self._post({'jsonrpc': '2.0', 'method': method})
        except Exception:
            pass

    def close(self):
        pass


def server_spec(c):
    """imported ones read fresh from claude code config"""
    if c.get('source') == 'Claude Code':
        with open(CLAUDE_CFG, encoding='utf-8') as f:
            spec = (json.load(f).get('mcpServers') or {}).get(c['server_name'])
        if not spec:
            raise RuntimeError('no longer in Claude Code')
        return spec
    return c['spec']


def open_mcp(c):
    spec = server_spec(c)
    cli = HttpMCP(spec) if spec.get('type') in ('http', 'sse') or spec.get('url') else StdioMCP(spec)
    cli.request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                               'clientInfo': {'name': 'master-interface', 'version': '1'}})
    cli.notify('notifications/initialized')
    return cli


def refresh(cid):
    """new tools come in off, known ones keep their setting"""
    c = load(cid)
    try:
        if c['via'] == 'api':
            m = builtin_modules().get(cid)
            found = {t['name']: {'description': t.get('description', ''), 'input': t.get('input', {}),
                                 'kind': t.get('kind') or tool_kind(t['name']), 'default': t.get('default', 'allow')} for t in m.MANIFEST['tools']}
            c['needs_key'] = m.MANIFEST.get('needs_key', '')
            c['status'] = 'connected' if m.status() else ('needs a key' if c['needs_key'] else 'needs sign in')
        else:
            cli = open_mcp(c)
            try:
                res = cli.request('tools/list')
            finally:
                cli.close()
            found = {t['name']: {'description': (t.get('description') or '').strip(), 'input': t.get('inputSchema', {}),
                                 'kind': tool_kind(t['name'], t.get('annotations')), 'default': 'off'} for t in res.get('tools', [])}
            c['status'] = 'connected'
        old = c.get('tools', {})
        c['tools'] = {n: {'description': t['description'], 'input': t['input'], 'kind': t['kind'],
                          'permission': old.get(n, {}).get('permission', t['default'])} for n, t in found.items()}
        c.pop('error', None)
    except Exception as e:
        c['status'] = 'error'
        c['error'] = str(e)[:300]
    c['checked'] = time.strftime('%Y-%m-%d %H:%M')
    save(c)
    return c


def import_claude_code():
    """every claude code mcp server becomes a connection, off till you look"""
    with open(CLAUDE_CFG, encoding='utf-8') as f:
        servers = json.load(f).get('mcpServers') or {}
    have = {c.get('server_name') for c in all_connections() if c.get('source') == 'Claude Code'}
    added = []
    for name, spec in servers.items():
        if name in have:
            continue
        cid = slug(name)
        save({'id': cid, 'name': name, 'via': 'mcp', 'source': 'Claude Code', 'server_name': name,
              'transport': 'http' if spec.get('url') else 'stdio', 'enabled': True, 'tools': {},
              'description': spec.get('url') or ' '.join([spec.get('command', '')] + list(spec.get('args') or []))[:120]})
        added.append(cid)
    return added


def add_mcp(name, url=None, command=None, args=None, headers=None):
    cid = slug(name)
    spec = {'type': 'http', 'url': url, 'headers': headers or {}} if url else {'command': command, 'args': args or []}
    save({'id': cid, 'name': name, 'via': 'mcp', 'source': 'added', 'spec': spec,
          'transport': 'http' if url else 'stdio', 'enabled': True, 'tools': {}, 'description': url or command})
    return cid


def set_enabled(cid, on):
    c = load(cid)
    c['enabled'] = bool(on)
    save(c)


def set_permission(cid, tool, perm):
    c = load(cid)
    # '*' all tools, '*read' / '*write' one group
    if tool in ('*', '*read', '*write'):
        names = [n for n, t in c.get('tools', {}).items() if tool == '*' or t.get('kind') == tool[1:]]
    else:
        names = [tool]
    for n in names:
        if n in c['tools'] and perm in ('allow', 'ask', 'off'):
            c['tools'][n]['permission'] = perm
    save(c)


def remove(cid):
    if cid in builtin_modules():
        raise RuntimeError('built-in connections can be switched off, not removed')
    os.remove(os.path.join(conn_dir(), cid + '.json'))


def tools_for_ai():
    """every tool not off on a switched on connection"""
    out = []
    for c in all_connections():
        if not c.get('enabled', True):
            continue
        for n, t in c.get('tools', {}).items():
            if t.get('permission') != 'off':
                out.append({'connection': c['id'], 'tool': n, 'permission': t['permission'],
                            'description': t['description'], 'input': t['input']})
    return out


def call(cid, tool, args):
    """refuses what is switched off"""
    c = load(cid)
    if not c.get('enabled', True):
        raise PermissionError(c['name'] + ' is switched off')
    t = c.get('tools', {}).get(tool)
    if not t or t.get('permission') == 'off':
        raise PermissionError(tool + ' is off for the AI')
    if c['via'] == 'api':
        return builtin_modules()[cid].call(tool, args or {})
    cli = open_mcp(c)
    try:
        return cli.request('tools/call', {'name': tool, 'arguments': args or {}}, timeout=120)
    finally:
        cli.close()
