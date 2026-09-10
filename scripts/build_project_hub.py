#!/usr/bin/env python3
"""Build a static project journal from public GitHub metadata and curated articles."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'site/projects'
OUT = ROOT / 'docs/projects'
OWNER = '2667741708'
PUBLIC_BASE = 'https://whm12.art/projects/'

def safe_url(value):
    parsed = urlsplit(str(value))
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Only public HTTPS links without credentials are supported')
    return escape(str(value), quote=True)

def slug_for(name, custom=None):
    slug = custom or name.lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', slug):
        slug = name.lower().strip('-_') if custom is None else slug
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', slug):
        raise ValueError('Invalid project slug: ' + slug)
    return slug

def sync():
    projects = []
    for page in range(1, 101):
        url = f'https://api.github.com/users/{OWNER}/repos?type=owner&sort=pushed&per_page=100&page={page}'
        request = Request(url, headers={'User-Agent':'whm-project-journal', 'Accept':'application/vnd.github+json'})
        with urlopen(request, timeout=30) as response:
            batch = json.load(response)
        for repo in batch:
            if repo.get('private') or repo.get('owner', {}).get('login') != OWNER:
                continue
            projects.append({
                'name': repo['name'], 'url': repo['html_url'],
                'description': repo.get('description') or '', 'language': repo.get('language') or '',
                'updated': repo.get('pushed_at') or '', 'fork': bool(repo.get('fork')),
                'archived': bool(repo.get('archived')), 'homepage': repo.get('homepage') or '',
                'topics': repo.get('topics') or [], 'private': False,
            })
        if len(batch) < 100:
            break
    else:
        raise RuntimeError('GitHub pagination limit exceeded')
    if not projects:
        raise RuntimeError('Empty public repository list; existing catalog was preserved')
    payload = {'owner': OWNER, 'synced_at': datetime.now(timezone.utc).isoformat(), 'projects': projects}
    temporary = SOURCE / 'catalog.json.tmp'
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(SOURCE / 'catalog.json')
    print(f'Synced {len(projects)} public repositories; no credentials or private repositories exported.')

def link(url, label, css=''):
    return f'<a class="{css}" href="{safe_url(url)}">{escape(label)}</a>'

def shell(title, description, body, *, depth=0, canonical=''):
    assets = '../' if depth else './'
    home = '../' if depth else './'
    return f'''<!doctype html>
<html lang="zh-CN" class="ios"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{escape(description, quote=True)}"><meta name="theme-color" content="#faf9f5">
<title>{escape(title)} · WHM 项目手记</title><link rel="canonical" href="{safe_url(PUBLIC_BASE + canonical)}">
<link rel="icon" href="{assets}favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="{assets}vendor/framework7/framework7-bundle.min.css"><link rel="stylesheet" href="{assets}vendor/framework7/framework7-default-theme.css"><link rel="stylesheet" href="{assets}hub.css"><script src="{assets}hub.js" defer></script></head>
<body><a class="skip" href="#main">跳到正文</a><div id="app"><div class="view view-main"><div class="page page-current">
<header class="navbar"><div class="navbar-bg"></div><nav class="navbar-inner" aria-label="站点导航"><div class="left"><a class="link" href="{home}">所有项目</a></div><div class="title">项目手记</div><div class="right"><button class="button" id="theme" type="button" aria-label="切换深浅主题">外观</button></div></nav></header>
<main id="main" class="page-content">{body}<footer class="block"><p>WHM 项目手记 · 从代码到实践</p><p><a href="https://github.com/{OWNER}">GitHub ↗</a> · UI: <a href="https://framework7.io/">Framework7 iOS</a></p></footer></main></div></div></div></body></html>'''

def summary_for(project, edit):
    summary = edit.get('summary') or project['description']
    if not summary or summary.count('?') > 5:
        return '这个公开项目尚未填写清晰简介。这里保留源码入口和项目资料，使用说明以仓库 README 为准。'
    return summary

def project_page(project, edit, slug):
    name = project['name']; title = edit.get('title', name); summary = summary_for(project, edit)
    category = edit.get('category', '项目探索')
    homepage = project.get('homepage', '')
    demo = link(homepage, '打开项目网站 ↗', 'button button-outline') if homepage.startswith('https://') else ''
    guide = link(edit['guide_url'], '阅读完整图文教程 →', 'button button-fill') if edit.get('guide_url') else ''
    fork = '<p class="notice">这是 Fork 仓库。上游作者与来源请查看 GitHub 仓库页面；此页不将上游成果表述为原创。</p>' if project.get('fork') else ''
    archived = '<p class="notice">此仓库已归档，使用前请检查维护状态。</p>' if project.get('archived') else ''
    kind = edit.get('kind', '项目速览')
    body = f'''<article class="project-article block"><p>{escape(category)} / {escape(kind)}</p><h1>{escape(title)}</h1><p>{escape(summary)}</p>
<div class="meta"><span>{escape(name)}</span><span>{escape(project['language'] or '语言未标注')}</span><span>仓库更新 {escape(project['updated'][:10])}</span></div>{fork}{archived}
<div class="actions">{guide}{link(project['url'], '查看 GitHub 源码 ↗', 'button button-outline')}{demo}</div>
<section><p class="eyebrow">01 / ABOUT THE PROJECT</p><h2>这个项目做什么</h2><p>{escape(summary)}</p><p>本页整理公开仓库的项目资料与阅读入口。功能范围、依赖条件和当前版本，请以仓库中的 README 与文档为准。</p></section>
<section><p class="eyebrow">02 / GETTING STARTED</p><h2>从哪里开始使用</h2><ol class="steps"><li><strong>先读 README。</strong>了解适用系统、安装依赖、配置方法和使用边界。</li><li><strong>查看代码与版本。</strong>如果仓库提供 Release，先核对发布说明；没有发布包时按仓库指引使用源码。</li><li><strong>记录自己的实践。</strong>复现过程中遇到的问题、截图和配置说明，可以逐步补充到这个项目的独立文章中。</li></ol><div class="reading">{link(project['url']+'#readme','阅读 README →')}{link(project['url']+'/releases','查看版本发布 →')}{link(project['url']+'/issues','查看问题与讨论 →')}</div></section>
<section><p class="eyebrow">03 / PROJECT NOTES</p><h2>{'继续阅读完整教程' if guide else '让实践记录跟上代码'}</h2><p>{'这个项目已有独立的图文教程，可以从下面继续阅读。' if guide else '当前页面是项目速览，尚未补充专门的实操长文。这里不预设未经验证的安装步骤或实验结论；后续文章可以沿用当前固定链接。'}</p>{guide}<p class="small">页面资料来自公开 GitHub 元数据；“仓库更新”指代码推送时间，不代表博客发布日期或现场验证时间。</p></section></article>'''
    return shell(title, summary, body, depth=1, canonical=slug+'/')

def build():
    catalog = json.loads((SOURCE/'catalog.json').read_text(encoding='utf-8'))
    edits = json.loads((SOURCE/'editorial.json').read_text(encoding='utf-8'))
    projects = catalog['projects']
    if any(p.get('private') or not p['url'].startswith(f'https://github.com/{OWNER}/') for p in projects):
        raise ValueError('Catalog contains a private or non-owned repository')
    slugs = [slug_for(p['name'], edits.get(p['name'], {}).get('slug')) for p in projects]
    if len(slugs) != len(set(slugs)):
        raise ValueError('Project slugs must be unique')
    output = Path(tempfile.mkdtemp(prefix='.project-hub-build-', dir=ROOT/'docs'))
    shutil.copytree(ROOT/'docs/vendor', output/'vendor')
    for name in ['hub.css','hub.js','favicon.svg']:
        shutil.copy2(SOURCE/name, output/name)
    cards=[]; features=[]
    for project, slug in zip(projects, slugs):
        edit = edits.get(project['name'], {})
        title = edit.get('title',project['name']); summary = summary_for(project,edit)
        category = edit.get('category', '项目探索'); kind = edit.get('kind','项目速览')
        directory = output/slug; directory.mkdir(exist_ok=True)
        if edit.get('article') == 'network-assist':
            article = (ROOT/'docs/blog/index.html').read_text(encoding='utf-8')
            article = article.replace('../images/', './images/')
            article = article.replace('<body>', '<body>\n<a class="hub-back" href="../">← 所有项目 · WHM 项目手记</a>')
            article = article.replace('</head>', f'<link rel="canonical" href="{PUBLIC_BASE}{slug}/">\n<link rel="icon" href="../favicon.svg" type="image/svg+xml">\n</head>')
            (directory/'index.html').write_text(article, encoding='utf-8')
            shutil.copy2(ROOT/'docs/blog/blog.js', directory/'blog.js')
            css = (ROOT/'docs/blog/style.css').read_text(encoding='utf-8')
            (directory/'style.css').write_text(css+'\n.hub-back{display:block;max-width:1280px;margin:16px auto;padding:0 24px;font-size:14px}\n', encoding='utf-8')
            images = directory/'images'; images.mkdir(exist_ok=True)
            for image in (ROOT/'docs/images').glob('*.png'):
                shutil.copy2(image, images/image.name)
        else:
            (directory/'index.html').write_text(project_page(project,edit,slug), encoding='utf-8')
        badges = ('<span>Fork</span>' if project.get('fork') else '') + ('<span>已归档</span>' if project.get('archived') else '')
        cards.append(f'''<li class="project-card" data-category="{escape(category,quote=True)}" data-kind="{escape(kind,quote=True)}" data-fork="{'true' if project.get('fork') else 'false'}"><a class="item-link item-content" href="./{slug}/"><div class="item-inner"><div class="item-title-row"><div class="item-title">{escape(title)}</div></div><div class="item-subtitle">{escape(category)} · {escape(kind)} {badges}</div><div class="item-text">{escape(summary)}</div><div class="item-footer">{escape(project['name'])} · {escape(project['language'] or '语言未标注')} · {escape(project['updated'][:10])}</div></div></a></li>''')
        if edit.get('featured'):
            features.append(f'''<article class="card"><div class="card-header">完整教程</div><div class="card-content card-content-padding"><h2>{escape(title)}</h2><p>{escape(summary)}</p></div><div class="card-footer"><a class="link" href="./{slug}/">阅读完整教程 →</a></div></article>''')
    categories = sorted({edits.get(p['name'],{}).get('category','项目探索') for p in projects})
    options = ''.join(f'<option>{escape(c)}</option>' for c in categories)
    count = len(projects); tutorials = sum(edits.get(p['name'],{}).get('kind')=='完整教程' for p in projects)
    body = f'''<header class="block"><h1>项目手记</h1><p>在一个地方，阅读代码背后的实现、教程与实践记录。</p><p>{count} 个公开项目 · {tutorials} 篇完整教程 · 更新于 {escape(catalog['synced_at'][:10])}</p></header>
<section aria-label="精选教程"><div class="block-title block-title-medium">精选教程</div><div class="grid grid-cols-1 medium-grid-cols-2">{''.join(features)}</div></section>
<section id="collection"><h2 class="block-title block-title-medium">所有项目</h2>
<form class="filters list list-strong list-dividers inset" role="search"><ul>
<li class="item-content item-input"><div class="item-inner"><label class="item-title item-label" for="search">搜索项目</label><div class="item-input-wrap"><input id="search" type="search" placeholder="搜索名称、用途、技术" autocomplete="off"></div></div></li>
<li class="item-content item-input"><div class="item-inner"><label class="item-title item-label" for="category">项目分类</label><div class="item-input-wrap input-dropdown-wrap"><select id="category"><option value="">全部分类</option>{options}</select></div></div></li>
<li class="item-content item-input"><div class="item-inner"><label class="item-title item-label" for="kind">文章类型</label><div class="item-input-wrap input-dropdown-wrap"><select id="kind"><option value="">全部文章</option><option>完整教程</option><option>项目速览</option></select></div></div></li>
<li><label class="item-checkbox item-content"><input id="include-forks" type="checkbox" checked><i class="icon icon-checkbox"></i><div class="item-inner"><div class="item-title">包含 Fork</div></div></label></li></ul></form>
<p id="result-count" class="block" role="status">{count} 个项目</p><div class="list media-list list-strong list-dividers inset"><ul>{''.join(cards)}</ul></div><p id="empty" class="block" hidden>没有匹配的项目。试试其他关键词，或清除筛选条件。</p></section>
<section class="block"><h2>持续记录，持续更新</h2><p>完整教程记录实现和操作过程；项目速览保留用途与源码入口。后续文章沿用各项目的固定地址。</p><a href="https://github.com/{OWNER}">在 GitHub 查看全部仓库 ↗</a></section>'''
    (output/'index.html').write_text(shell('项目索引', '统一浏览 WHM 的公开 GitHub 项目、独立博客与使用教程。',body), encoding='utf-8')
    urls=[PUBLIC_BASE]+[PUBLIC_BASE+s+'/' for s in slugs]
    (output/'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+escape(u)+'</loc></url>' for u in urls)+'</urlset>',encoding='utf-8')
    # Retain an explicit manifest; deployment uses only this generated tree.
    (output/'manifest.json').write_text(json.dumps({'projects':count,'tutorials':tutorials,'paths':[s+'/' for s in slugs],'synced_at':catalog['synced_at']},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Replace only our generated tree. Removed/private repositories must not leave live pages behind.
    if OUT.resolve() != (ROOT/'docs/projects').absolute() or OUT.is_symlink():
        raise ValueError('Refusing to replace an unexpected output path')
    if OUT.exists():
        if not (OUT/'manifest.json').is_file():
            raise ValueError('Output is not a generated project hub; refusing to remove it')
        shutil.rmtree(OUT)
    output.replace(OUT)
    print(f'Built {count} project pages and unified index in {OUT}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sync', action='store_true', help='Refresh public GitHub metadata before building')
    args = parser.parse_args()
    if args.sync: sync()
    build()
