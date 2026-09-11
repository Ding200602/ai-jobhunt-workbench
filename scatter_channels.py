# -*- coding: utf-8 -*-
"""
零散渠道采集器（scatter_channels）

定位：jobbot 工作台「搜岗」的可插拔零散源模块，专门收集"非集中招聘平台"的分散信息：
  1) province_campus  省级毕业生就业服务平台 · 高校校招公告（各校就业网发布，聚合于省级平台）
  2) province_job     省级毕业生就业服务平台 · 单位自主发布的零散岗位（id 空间遍历）
  3) province_fair    省级毕业生就业服务平台 · 校园招聘会/双选会排期
  4) campus2026       社区维护的校招入口汇总文件（GitHub markdown 表格，含内推/推文链接）

省级平台 BASE_URL 按你所在省份替换即可（同类平台结构高度相似，改 URL + 正则即可复用）。
输出：统一 schema 的 records（source/kind/job_name/company/city/salary/meta/date/link/raw）
"""
import re
import json
import time
import gzip
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
      'Accept-Language': 'zh-CN,zh;q=0.9'}

FIELDS = ['source', 'kind', 'job_name', 'company', 'city', 'salary', 'meta', 'date', 'link', 'raw']


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    r = urllib.request.urlopen(req, timeout=timeout)
    d = r.read()
    if r.headers.get('Content-Encoding') == 'gzip':
        d = gzip.decompress(d)
    return d.decode('utf-8', 'replace')


def _rec(**kw):
    r = {k: '' for k in FIELDS}
    r.update(kw)
    return r


def _flat(html):
    t = re.sub(r'<(script|style).*?</\1>', ' ', html, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t).replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', t)


# ---------- 1) 省级平台 · 校招公告列表（分页公开，BASE_URL 替换为对应省份） ----------
PROV_BASE = 'https://job.example-prov.cn'

CAMPUS_ITEM = re.compile(
    r'<ul>\s*<li[^>]*><a href="(/campus/view/id/\d+)"[^>]*>(.*?)</a></li>\s*'
    r'<li[^>]*><span>(.*?)</span></li>\s*<li[^>]*><span>(.*?)</span></li>\s*'
    r'<li[^>]*><span>(.*?)</span></li>', re.S)
FAIR_ITEM = re.compile(
    r'<ul>\s*<li[^>]*><a href="(/jobfair/view/id/\d+)"[^>]*>(.*?)</a></li>\s*'
    r'<li[^>]*><span>(.*?)</span></li>\s*<li[^>]*><span>(\d{4}-\d{2}-\d{2})</span></li>', re.S)


def fetch_prov_campus(pages=20, delay=0.25):
    """抓 /campus 公告列表最近 N 页（每页 20 条）。"""
    out, seen = [], set()
    for p in range(1, pages + 1):
        url = f'{PROV_BASE}/campus' + (f'?page={p}' if p > 1 else '')
        try:
            html = _get(url)
        except Exception:
            continue
        for href, title, school, city, date in CAMPUS_ITEM.findall(html):
            t = re.sub(r'<[^>]+>', ' ', title)
            t = re.sub(r'\s+', ' ', t).strip()
            if not t or len(t) < 4:
                continue
            full = urllib.parse.urljoin(PROV_BASE, href)
            if full in seen:
                continue
            seen.add(full)
            out.append(_rec(source='province_campus', kind='campus_notice', job_name=t,
                            company=school.strip() or t, city=city.strip(), date=date.strip(),
                            link=full, raw=f'{t} | {school.strip()} | {date.strip()}'))
        time.sleep(delay)
    return out


# ---------- 2) 省级平台 · 零散岗位（id 空间遍历） ----------
_JOB_FIELDS = {
    'job_name': r'详情\s*(.+?)\s*分享至',
    'company': r'分享至：?\s*(.+?)\s*单位性质',
    'salary': r'月薪：\s*(.+?)\s*招聘人数',
    'date': r'发布时间：\s*([\d-]+ [\d:]+)',
    'city': r'工作地点：\s*(.+?)\s*学历要求',
}


def _parse_job(html):
    t = _flat(html)
    if '月薪：' not in t:
        return None
    d = {}
    for k, pat in _JOB_FIELDS.items():
        m = re.search(pat, t)
        d[k] = m.group(1).strip() if m else ''
    edu = re.search(r'学历要求：\s*(.+?)\s*工作经验', t)
    exp = re.search(r'工作经验：\s*(.+?)\s*语言能力', t)
    d['meta'] = ' '.join(x for x in [exp.group(1).strip() if exp else '', edu.group(1).strip() if edu else ''] if x)
    return d if d['job_name'] else None


def fetch_prov_jobs(id_start, id_end, workers=6, delay=0.1):
    """遍历 job id 空间（自增稀疏，命中率约 6-7%）。"""
    out, lock = [], None

    def _one(i):
        url = f'{PROV_BASE}/job/view/id/{i}'
        try:
            html = _get(url, timeout=15)
        except Exception:
            return None
        d = _parse_job(html)
        if not d:
            return None
        d.update(source='province_job', kind='job', link=url, raw=d.get('job_name', ''))
        return _rec(**d)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, i) for i in range(id_start, id_end, -1)]
        for f in as_completed(futs):
            r = f.result()
            if r:
                out.append(r)
    out.sort(key=lambda x: x.get('date', ''), reverse=True)
    return out


# ---------- 3) 省级平台 · 招聘会/双选会 ----------
def fetch_prov_fair(pages=5, delay=0.25):
    out, seen = [], set()
    for p in range(1, pages + 1):
        url = f'{PROV_BASE}/jobfair' + (f'?page={p}' if p > 1 else '')
        try:
            html = _get(url)
        except Exception:
            continue
        for href, name, school, date in FAIR_ITEM.findall(html):
            name, school, date = name.strip(), school.strip(), date.strip()
            key = (name, date)
            if key in seen or not name:
                continue
            seen.add(key)
            out.append(_rec(source='province_fair', kind='fair', job_name=name,
                            company=school, date=date, link=urllib.parse.urljoin(PROV_BASE, href),
                            raw=f'{name} | {school} | {date}'))
        time.sleep(delay)
    return out


# ---------- 4) GitHub 社区维护的校招汇总文件 ----------
CAMPUS2026 = 'https://cdn.jsdelivr.net/gh/byby221b/Campus2026@main/README.md'


def fetch_campus2026(url=CAMPUS2026):
    try:
        md = _get(url)
    except Exception:
        return []
    out, section = [], ''
    for line in md.splitlines():
        line = line.rstrip()
        if line.startswith('##') or line.startswith('###'):
            section = line.strip('# ').strip()
            continue
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) < 4 or set(cells[0]) <= set('- :'):
            continue
        if cells[0] in ('公司',):
            continue
        company = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', cells[0])
        link_m = re.search(r'\((https?://[^)]+)\)', cells[1])
        link = link_m.group(1) if link_m else ''
        note = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', cells[4]) if len(cells) > 4 else ''
        out.append(_rec(source='campus2026', kind='entry', job_name=section, company=company,
                        date=cells[2] if len(cells) > 2 else '', city=cells[3] if len(cells) > 3 else '',
                        link=link, raw=note))
    return out


def collect_all(campus_pages=20, job_id_start=1335000, job_id_end=1334300, out_json=None):
    data = []
    data += fetch_prov_campus(pages=campus_pages)
    data += fetch_prov_jobs(job_id_start, job_id_end)
    data += fetch_prov_fair()
    data += fetch_campus2026()
    if out_json:
        with open(out_json, 'w', encoding='utf-8') as f:
            json.dump({'updated': time.strftime('%Y-%m-%d %H:%M:%S'), 'total': len(data), 'records': data},
                      f, ensure_ascii=False, indent=2)
    return data


if __name__ == '__main__':
    import collections
    recs = collect_all()
    c = collections.Counter(r['source'] for r in recs)
    print('total', len(recs), dict(c))
    for r in recs[:8]:
        print(' ', r['source'], '|', r['date'], '|', r['job_name'][:30], '|', r['company'][:22])
