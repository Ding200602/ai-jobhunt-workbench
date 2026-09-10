# -*- coding: utf-8 -*-
"""
投递工作台 · 核心引擎
用 DrissionPage 控制独立 Edge 实例；所有步骤/日志/截图通过 Hub 广播给可视化面板。
"""
import os
import json
import time
import threading
from DrissionPage import ChromiumPage, ChromiumOptions

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SHOT_DIR = os.path.join(BASE_DIR, 'shots')
USER_DATA = os.path.join(BASE_DIR, 'edge_profile')

EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]
EDGE = next((p for p in EDGE_CANDIDATES if os.path.exists(p)), None)


class Hub:
    """事件总线：引擎产生的每步事件都推给所有已连接的面板客户端。"""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()
        self.subscribers = []

    def emit(self, type_, payload):
        ev = {'t': time.time(), 'type': type_, 'payload': payload}
        with self.lock:
            self.events.append(ev)
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put(ev)
            except Exception:
                pass

    def register(self, q):
        with self.lock:
            self.subscribers.append(q)
            snapshot = list(self.events)
        for ev in snapshot:
            try:
                q.put(ev)
            except Exception:
                pass


hub = Hub()


class Bot:
    """浏览器控制层。action 白名单，禁止任意代码执行。"""

    def __init__(self):
        self.page = None
        self.busy = False
        self.lock = threading.Lock()

    def _ensure(self):
        if self.page is None:
            hub.emit('log', {'msg': '启动 Edge（独立配置，可保留登录态）...'})
            co = ChromiumOptions()
            if EDGE:
                co.set_browser_path(EDGE)
            os.makedirs(USER_DATA, exist_ok=True)
            co.set_user_data_path(USER_DATA)
            co.set_argument('--window-size=1360,900')
            self.page = ChromiumPage(co)
            hub.emit('log', {'msg': '浏览器已就绪'})
        return self.page

    def open(self, url, timeout=15.0, no_shot=False):
        p = self._ensure()
        hub.emit('step', {'msg': f'打开 {url}'})
        try:
            p.get(url, timeout=timeout)
        except Exception as e:
            hub.emit('log', {'msg': f'页面加载超过{timeout}s，继续执行（{type(e).__name__}）'})
        time.sleep(2.0)
        hub.emit('log', {'msg': f'页面标题：{p.title}'})
        if no_shot:
            return {'ok': True}
        return self.shot()

    def shot(self):
        p = self._ensure()
        os.makedirs(SHOT_DIR, exist_ok=True)
        path = os.path.join(SHOT_DIR, f'shot_{int(time.time() * 1000)}.png')
        p.get_screenshot(path=path)
        hub.emit('shot', {'msg': '已截图', 'path': path})
        return path

    def title(self):
        return self._ensure().title

    def _auto_shot(self):
        """动作后的自动截图（失败不阻塞主流程）。"""
        try:
            return self.shot()
        except Exception as e:
            hub.emit('error', {'msg': f'自动截图失败：{e}'})
            return None

    def _collect(self, loc, fields, limit=200, timeout=5):
        """按 css 定位符批量提取当前页列表数据。
        loc: 列表条目 css 选择器
        fields: 字段映射 {'字段名': 'css:.xx' 或 'css:.xx@attr:href'}
        返回: (items, count, err)
        """
        p = self._ensure()
        items = []
        try:
            nodes = p.eles(f'css:{loc}', timeout=timeout)
        except Exception as e:
            return [], 0, f'定位列表失败: {e}'
        for node in nodes[:limit]:
            row = {}
            try:
                row['raw'] = node.text
            except Exception:
                row['raw'] = ''
            for name, spec in fields.items():
                try:
                    if '@' in spec:
                        css, _, attr = spec.partition('@')
                        css = css.strip()
                        sub = node.ele(f'css:{css}', timeout=0.5)
                        row[name] = sub.attr(attr) if sub else ''
                    else:
                        sub = node.ele(f'css:{spec}', timeout=0.5)
                        row[name] = (sub.text or '').strip() if sub else ''
                except Exception:
                    row[name] = ''
            items.append(row)
        return items, len(items), None

    def act(self, action, params):
        with self.lock:
            if self.busy:
                return {'ok': False, 'msg': '引擎忙，请稍候'}
            self.busy = True
        try:
            p = self._ensure()
            if action == 'open':
                return {'ok': True, **self.open(params.get('url', ''), no_shot=bool(params.get('no_shot')))}
            if action == 'shot':
                return {'ok': True, 'shot': self.shot()}
            if action == 'title':
                return {'ok': True, 'title': self.title()}
            if action == 'sleep':
                time.sleep(max(0.1, float(params.get('sec', 1))))
                return {'ok': True}
            if action == 'back':
                p.back()
                hub.emit('step', {'msg': '后退一页'})
                return {'ok': True, 'shot': self._auto_shot()}
            if action == 'refresh':
                p.refresh()
                time.sleep(1.5)
                hub.emit('step', {'msg': '刷新页面'})
                return {'ok': True, 'shot': self._auto_shot()}
            if action == 'click':
                loc = params.get('loc', '')
                timeout = max(2, float(params.get('timeout', 10)))
                if not loc:
                    return {'ok': False, 'msg': 'click 需要 loc 参数'}
                ele = p.ele(loc, timeout=timeout)
                hub.emit('step', {'msg': f'点击：{loc}'})
                ele.click()
                time.sleep(1.0)
                return {'ok': True, 'shot': self._auto_shot()}
            if action == 'type':
                loc = params.get('loc', '')
                text = params.get('text', '')
                timeout = max(2, float(params.get('timeout', 10)))
                if not loc:
                    return {'ok': False, 'msg': 'type 需要 loc 参数'}
                ele = p.ele(loc, timeout=timeout)
                hub.emit('step', {'msg': f'输入：{text[:40]}'})
                ele.clear()
                ele.input(text)
                time.sleep(0.5)
                return {'ok': True, 'shot': self._auto_shot()}
            if action == 'set_upload':
                fp = params.get('file', '')
                if not fp or not os.path.exists(fp):
                    return {'ok': False, 'msg': f'文件不存在：{fp}'}
                hub.emit('step', {'msg': f'预设上传文件：{os.path.basename(fp)}'})
                p.set.upload_files(fp)
                return {'ok': True}
            if action == 'js':
                code = params.get('code', '')
                if not code:
                    return {'ok': False, 'msg': 'js 需要 code 参数'}
                hub.emit('step', {'msg': f'执行页面脚本：{code[:60]}'})
                result = p.run_js(code)
                return {'ok': True, 'result': str(result), 'shot': self._auto_shot()}
            if action == 'collect':
                loc = params.get('loc', '')
                fields = params.get('fields', {})
                if not loc or not isinstance(fields, dict):
                    return {'ok': False, 'msg': 'collect 需要 loc 与 fields 参数'}
                items, count, err = self._collect(loc, fields, int(params.get('limit', 200)), float(params.get('timeout', 5)))
                hub.emit('step', {'msg': f'批量提取 {count} 条 @ {loc}'})
                return {'ok': True, 'count': count, 'err': err, 'items': items, 'shot': self._auto_shot()}
            if action == 'scroll_down':
                p.scroll.down(int(params.get('px', 600)))
                time.sleep(0.6)
                return {'ok': True}
            if action == 'scroll_bottom':
                p.scroll.to_bottom()
                time.sleep(0.8)
                return {'ok': True}
            if action == 'get_url':
                return {'ok': True, 'url': p.url}
            if action == 'wait':
                time.sleep(max(0.1, float(params.get('sec', 1))))
                return {'ok': True, 'title': p.title}
            return {'ok': False, 'msg': f'未知动作：{action}'}
        except Exception as e:
            hub.emit('error', {'msg': repr(e)})
            return {'ok': False, 'msg': repr(e)}
        finally:
            self.busy = False


bot = Bot()
