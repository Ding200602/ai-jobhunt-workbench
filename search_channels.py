# -*- coding: utf-8 -*-
"""
全面搜索 · 多渠道岗位采集器（search_channels）

作用：把 s5「搜岗过滤」从单一 BOSS 渠道扩展为多渠道聚合。
每个渠道 = 一个插件（dict 描述）：搜索 URL 模板 + 列表定位符 + 字段映射 + 翻页方式。
统一输出 schema 的岗位记录，后续可写入 jobs_batch.json / 汇总 Excel / 过滤规则。

schema:
  {
    "source": "zhaopin|boss|51job|shixiseng|yingjiesheng|nowcoder|...",
    "kind":   "job"  (具体岗位) | "entry" (校招官网入口, 公司级) ,
    "job_name": 岗位名, "company": 公司, "city": 城市,
    "salary": 薪资文本, "meta": 学历/经验等标签(空格分隔),
    "date": 发布日期文本, "link": 落地页/投递链接, "raw": 原始文本
  }
"""
import os
import re
import time
import json
import urllib.request
import urllib.parse
import ssl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(BASE_DIR, 'jobs_batch_multi.json')

# ---------- 统一岗位字段顺序（写 Excel 用） ----------
FIELDS = ['source', 'kind', 'job_name', 'company', 'city', 'salary', 'meta', 'date', 'link', 'raw']


def norm(rec):
    r = {k: '' for k in FIELDS}
    r.update(rec)
    return r


# ---------- 渠道注册表 ----------
# loc: 列表条目 css；fields 见 bot._collect 的规范（@attr 表示取属性）
# page_param: 翻页 URL 参数名（第 2 页起拼接），多页翻页用 URL 而非点击，更稳
CHANNELS = {
    'boss': {
        'name': 'BOSS直聘',
        'url': 'https://www.zhipin.com/web/geek/job?query={kw}&city={city}',
        'cities': {'北京': '100010000', '长春': '101060100', '济南': '101120100', '全国': '100010000'},
        'loc': '.job-card-wrapper',
        'fields': {'job_name': '.job-name', 'company': '.company-name', 'salary': '.salary',
                   'meta': '.job-info', 'link': '.job-card-left@href'},
        'page_param': 'page={n}',
        'max_page': 10,
        'delay': 2.5,
    },
    'zhaopin': {
        'name': '智联招聘',
        'url': 'https://sou.zhaopin.com/?kw={kw}&jl={city}&kt=3',
        'cities': {'北京': '530', '长春': '732', '济南': '679', '全国': ''},
        'loc': '.joblist-box__item',
        'fields': {'job_name': '.joblist-box__iteminfo--name', 'company': '.company_name',
                   'salary': '.salary', 'meta': '.joblist-box__iteminfo--desc',
                   'link': '.joblist-box__iteminfo@href'},
        'page_param': 'p={n}',
        'max_page': 15,
        'delay': 2.0,
    },
    '51job': {
        'name': '前程无忧',
        'url': 'https://we.51job.com/pc/search?jobArea={city}&keyword={kw}&searchType=2',
        'cities': {'北京': '010000', '长春': '070200', '济南': '070300', '全国': '000000'},
        'loc': '.joblist-boxe .e',
        'fields': {'job_name': '.jname', 'company': '.cname', 'salary': '.sal',
                   'meta': '.info', 'link': 'a@href'},
        'page_param': 'pageNum={n}',
        'max_page': 10,
        'delay': 2.5,
    },
    'shixiseng': {
        'name': '实习僧',
        'url': 'https://www.shixiseng.com/interns?keyword={kw}&city={city}',
        'cities': {'北京': '全国', '长春': '全国', '济南': '全国', '全国': '全国'},
        'loc': '.intern-item',
        'fields': {'job_name': '.title', 'company': '.company', 'salary': '.money',
                   'meta': '.tags', 'link': 'a@href'},
        'page_param': 'p={n}',
        'max_page': 10,
        'delay': 2.0,
    },
    'nowcoder': {
        'name': '牛客校招',
        'url': 'https://www.nowcoder.com/jobs/school/search?query={kw}',
        'cities': {},
        'loc': '.job-item',
        'fields': {'job_name': '.job-name', 'company': '.company-name', 'salary': '.salary',
                   'meta': '.job-desc', 'link': 'a@href'},
        'page_param': 'page={n}',
        'max_page': 5,
        'delay': 2.0,
    },
}

# 目标城市
CITY_ALIAS = {'北京': '北京', '长春': '长春', '济南': '济南'}


def build_url(channel_id, kw, city='北京', page=1):
    ch = CHANNELS[channel_id]
    code = ch['cities'].get(city, '')
    url = ch['url'].format(kw=urllib.parse.quote(kw), city=code)
    if page > 1:
        if '?' in url:
            url = url.rstrip('&') + '&' + ch['page_param'].format(n=page)
        else:
            url = url.rstrip('/') + '?' + ch['page_param'].format(n=page)
    return url


# ---------- 应届生网（官网/ATS 校招入口聚合，服务端渲染可直抓） ----------
_SX_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
          'Accept-Language': 'zh-CN,zh;q=0.9'}
_SX_CTX = ssl.create_default_context()
_SX_CTX.check_hostname = False
_SX_CTX.verify_mode = ssl.CERT_NONE


def _fetch(url):
    req = urllib.request.Request(url, headers=_SX_UA)
    with urllib.request.urlopen(req, timeout=20, context=_SX_CTX) as r:
        return r.read().decode('utf-8', errors='ignore')


def fetch_yingjiesheng_entries(url='https://www.yingjiesheng.com/'):
    """抓应届生网首页的校招/官网 ATS 入口列表（entry 级情报）。"""
    try:
        html = _fetch(url)
    except Exception as e:
        return [], f'fetch err: {e}'
    txt = re.sub(r'<script.*?</script>', '', html, flags=re.S)
    txt = re.sub(r'<style.*?</style>', '', txt, flags=re.S)
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', txt)
    seen = set()
    out = []
    for href, title in links:
        t = re.sub(r'<[^>]+>', '', title).strip()
        if not t or len(t) > 40:
            continue
        if not (('招聘' in t or '实习' in t or '校招' in t) and len(t) >= 6):
            continue
        if href in seen:
            continue
        seen.add(href)
        full = urllib.parse.urljoin(url, href)
        out.append(norm({'source': 'yingjiesheng', 'kind': 'entry', 'company': t, 'job_name': '',
                         'link': full, 'date': ''}))
    return out, None


def save(records):
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({'updated': time.strftime('%Y-%m-%d %H:%M:%S'), 'total': len(records), 'records': records},
                  f, ensure_ascii=False, indent=2)
    return OUT_JSON
