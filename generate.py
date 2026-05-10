#!/usr/bin/env python3
"""
ClinicaUpdate — gerador semanal automático
Busca artigos no PubMed e gera HTML com curadoria da semana.
"""

import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

# ── configuração ────────────────────────────────────────────────────────────
TOPICS = [
    ("sepsis management treatment", "Sepse"),
    ("heart failure randomized trial", "ICC"),
    ("community acquired pneumonia antibiotic", "Pneumonia"),
    ("acute kidney injury", "LRA"),
    ("venous thromboembolism pulmonary embolism", "TEP/TVP"),
    ("hypertension treatment randomized", "HAS"),
    ("atrial fibrillation management", "FA"),
    ("type 2 diabetes mellitus treatment", "DM2"),
    ("acute pancreatitis management", "Pancreatite"),
    ("liver cirrhosis complication", "Cirrose"),
]

PREFERRED_PUBTYPES = [
    "randomized controlled trial",
    "meta-analysis",
    "systematic review",
    "clinical trial, phase iii",
    "clinical trial, phase ii",
]

DATE_FROM = (datetime.now() - timedelta(days=90)).strftime("%Y/%m/%d")
DATE_TO   = datetime.now().strftime("%Y/%m/%d")
MAX_PER_TOPIC = 3
BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def fetch(url, retries=3, delay=1.5):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                print(f"  [erro] {url[:80]}… → {e}")
                return b""


def search_pmids(query, date_from, date_to, max_results=5):
    q = urllib.parse.quote(
        f'({query})[Title/Abstract] AND ("{date_from}"[pdat]:"{date_to}"[pdat])'
    )
    url = f"{BASE_URL}/esearch.fcgi?db=pubmed&term={q}&retmax={max_results}&sort=relevance&retmode=json"
    data = fetch(url)
    if not data:
        return []
    try:
        return json.loads(data).get("esearchresult", {}).get("idlist", [])
    except Exception:
        return []


def fetch_summaries(pmids):
    if not pmids:
        return {}
    url = f"{BASE_URL}/esummary.fcgi?db=pubmed&id={','.join(pmids)}&retmode=json"
    data = fetch(url)
    if not data:
        return {}
    try:
        return json.loads(data).get("result", {})
    except Exception:
        return {}


def fetch_abstracts_and_dois(pmids):
    if not pmids:
        return {}, {}
    url = f"{BASE_URL}/efetch.fcgi?db=pubmed&id={','.join(pmids)}&rettype=abstract&retmode=xml"
    data = fetch(url)
    if not data:
        return {}, {}
    abs_map, doi_map = {}, {}
    try:
        root = ET.fromstring(data)
        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//PMID")
            if pmid_el is None:
                continue
            pmid = pmid_el.text or ""
            # abstract
            parts = article.findall(".//AbstractText")
            abs_parts = []
            for p in parts:
                label = p.get("Label", "")
                text = (p.text or "").strip()
                if text:
                    abs_parts.append(f"{label}: {text}" if label else text)
            if abs_parts:
                abs_map[pmid] = " ".join(abs_parts)
            # doi
            for aid in article.findall(".//ArticleId"):
                if aid.get("IdType") == "doi" and aid.text:
                    doi_map[pmid] = aid.text.strip()
    except Exception as e:
        print(f"  [erro XML] {e}")
    return abs_map, doi_map


def score_article(summary, pubtype_list):
    """Score de relevância: prioriza RCTs e meta-análises."""
    score = 0
    types_lower = [t.lower() for t in pubtype_list]
    for i, pt in enumerate(PREFERRED_PUBTYPES):
        if pt in types_lower:
            score += (len(PREFERRED_PUBTYPES) - i) * 10
            break
    # journals de alto impacto
    journal = (summary.get("fulljournalname") or "").lower()
    HIGH_IMPACT = ["new england journal", "lancet", "jama", "british medical journal",
                   "annals of internal medicine", "nature medicine", "bmj"]
    if any(j in journal for j in HIGH_IMPACT):
        score += 20
    return score


def collect_articles():
    """Coleta e deduplica artigos de todos os tópicos."""
    seen_pmids = set()
    articles = []

    for query, label in TOPICS:
        print(f"  → buscando: {label}")
        pmids = search_pmids(query, DATE_FROM, DATE_TO, max_results=MAX_PER_TOPIC + 2)
        pmids = [p for p in pmids if p not in seen_pmids]
        if not pmids:
            continue

        summaries = fetch_summaries(pmids)
        abs_map, doi_map = fetch_abstracts_and_dois(pmids)
        time.sleep(0.4)  # respeita rate limit do NCBI

        for pmid in pmids:
            s = summaries.get(pmid)
            if not s or pmid == "uids":
                continue
            abstract = abs_map.get(pmid, "")
            if not abstract:
                continue  # sem abstract, pula
            pubtypes = s.get("pubtype", [])
            score = score_article(s, pubtypes)
            articles.append({
                "pmid": pmid,
                "title": s.get("title", "").rstrip("."),
                "journal": s.get("fulljournalname") or s.get("source", ""),
                "pub_date": s.get("pubdate", ""),
                "authors": s.get("authors", []),
                "abstract": abstract,
                "pubtype": pubtypes,
                "doi": doi_map.get(pmid, ""),
                "topic_label": label,
                "score": score,
            })
            seen_pmids.add(pmid)

    # ordena por score e pega os melhores
    articles.sort(key=lambda a: a["score"], reverse=True)
    return articles[:8]


# ── HTML helpers ─────────────────────────────────────────────────────────────

def pubtype_badge(pubtypes):
    t = " ".join(pubtypes).lower()
    if "randomized" in t or "phase iii" in t or "phase ii" in t:
        return '<span class="tag tag-rct">RCT</span>'
    if "meta-analysis" in t:
        return '<span class="tag tag-meta">Meta-análise</span>'
    if "systematic review" in t:
        return '<span class="tag tag-meta">Rev. Sistemática</span>'
    if "review" in t:
        return '<span class="tag tag-review">Review</span>'
    return '<span class="tag tag-obs">Estudo</span>'


def fmt_authors(authors):
    names = [a.get("name", "") for a in authors[:3] if a.get("name")]
    result = ", ".join(names)
    return result + " et al." if len(authors) > 3 else result


def article_link(art):
    if art["doi"]:
        return f"https://doi.org/{art['doi']}"
    return f"https://pubmed.ncbi.nlm.nih.gov/{art['pmid']}"


def render_article_card(art, index):
    badge    = pubtype_badge(art["pubtype"])
    journal  = art["journal"][:45] + "…" if len(art["journal"]) > 45 else art["journal"]
    abstract = art["abstract"][:900] + "…" if len(art["abstract"]) > 900 else art["abstract"]
    authors  = fmt_authors(art["authors"])
    link     = article_link(art)
    label    = art.get("topic_label", "")

    return f"""
    <div class="article">
      <div class="art-meta">
        {badge}
        {f'<span class="tag tag-j">{journal}</span>' if journal else ''}
        {f'<span class="tag tag-y">{art["pub_date"]}</span>' if art["pub_date"] else ''}
        {f'<span class="tag tag-topic">{label}</span>' if label else ''}
      </div>
      <div class="art-title">{art["title"]}</div>
      <div class="art-summary">{abstract}</div>
      <div class="art-footer">
        <a class="art-link" href="{link}" target="_blank">↗ Artigo completo (PubMed)</a>
        <div class="art-authors">{authors}{" · " + art["pub_date"] if art["pub_date"] else ""}</div>
      </div>
    </div>"""


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--ink:#1a1c22;--ink2:#3a3d4a;--ink3:#6b7080;--paper:#f8f6f0;--paper2:#f0ede4;--rule:#ddd8cc;--red:#c0392b;--blue:#1a3d6b;--gold:#9a6b00}
html{scroll-behavior:smooth}
body{background:var(--paper);color:var(--ink);font-family:'IBM Plex Sans',sans-serif;font-size:16px;line-height:1.7}
.masthead{background:var(--ink);color:var(--paper);padding:0 40px;display:flex;align-items:stretch;border-bottom:3px solid var(--red)}
.masthead-left{flex:1;padding:26px 0;border-right:1px solid #333}
.masthead-right{padding:26px 0 26px 32px;display:flex;flex-direction:column;justify-content:center;gap:5px;min-width:220px}
.pub-name{font-family:'Playfair Display',serif;font-size:2.4rem;font-weight:600;letter-spacing:-0.5px;line-height:1;color:#fff}
.pub-name span{color:var(--red)}
.pub-sub{font-family:'IBM Plex Mono',monospace;font-size:0.68rem;color:#888;letter-spacing:0.15em;text-transform:uppercase;margin-top:6px}
.meta-item{font-family:'IBM Plex Mono',monospace;font-size:0.68rem;color:#888;letter-spacing:0.06em}
.meta-item strong{color:#ccc}
.page{max-width:900px;margin:0 auto;padding:40px 24px 80px}
.section-head{display:flex;align-items:baseline;gap:16px;margin-bottom:22px;padding-bottom:10px;border-bottom:2px solid var(--ink)}
.section-head h2{font-family:'Playfair Display',serif;font-size:1.4rem;font-weight:600}
.section-head .count{font-family:'IBM Plex Mono',monospace;font-size:0.66rem;color:var(--ink3);letter-spacing:0.08em}
.article{background:#fff;border:1px solid var(--rule);border-left:4px solid transparent;padding:22px 24px;margin-bottom:14px;transition:border-left-color .2s,box-shadow .2s}
.article:hover{border-left-color:var(--red);box-shadow:2px 4px 18px rgba(0,0,0,0.07)}
.art-meta{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.tag{font-family:'IBM Plex Mono',monospace;font-size:0.62rem;letter-spacing:0.07em;text-transform:uppercase;padding:2px 7px;border-radius:2px;font-weight:500}
.tag-rct{background:#e8f0ff;color:#1a3d8f;border:1px solid #b8ceff}
.tag-meta{background:#e8f8ee;color:#1a6b3c;border:1px solid #a8ddb8}
.tag-review{background:#fff8e0;color:#7a5200;border:1px solid #f0d880}
.tag-obs{background:var(--paper2);color:var(--ink2);border:1px solid var(--rule)}
.tag-j{background:var(--paper2);color:var(--ink2);border:1px solid var(--rule)}
.tag-y{background:transparent;color:var(--ink3);border:none;font-size:0.63rem}
.tag-topic{background:#f5e8ff;color:#5a1a8f;border:1px solid #ddb8ff}
.art-title{font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:600;color:var(--ink);line-height:1.35;margin-bottom:10px}
.art-summary{font-size:0.87rem;line-height:1.78;color:var(--ink2);margin-bottom:13px}
.art-footer{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.art-link{font-family:'IBM Plex Mono',monospace;font-size:0.68rem;color:var(--blue);text-decoration:none;border-bottom:1px solid currentColor;padding-bottom:1px;transition:color .15s}
.art-link:hover{color:var(--red)}
.art-authors{font-size:0.72rem;color:var(--ink3);font-style:italic;flex:1}
.archive-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:4px}
.archive-card{background:#fff;border:1px solid var(--rule);padding:14px 16px;text-decoration:none;color:var(--ink);transition:border-color .15s,box-shadow .15s;display:block}
.archive-card:hover{border-color:var(--red);box-shadow:1px 3px 10px rgba(0,0,0,0.06)}
.archive-date{font-family:'IBM Plex Mono',monospace;font-size:0.68rem;color:var(--ink3);margin-bottom:4px}
.archive-label{font-family:'Playfair Display',serif;font-size:0.95rem;font-weight:600}
.archive-count{font-family:'IBM Plex Mono',monospace;font-size:0.63rem;color:var(--ink3);margin-top:4px}
.divider{border:none;border-top:1px solid var(--rule);margin:32px 0}
.footer{background:var(--ink);color:#555;padding:18px 40px;font-family:'IBM Plex Mono',monospace;font-size:0.67rem;letter-spacing:0.04em;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
.footer a{color:#777}
@media(max-width:600px){.masthead{padding:0 18px;flex-direction:column}.masthead-right{padding:0 0 18px;border:none}.page{padding:22px 14px 60px}.article{padding:16px 14px}.pub-name{font-size:1.8rem}}
"""


def build_edition_html(articles, edition_date, all_editions):
    """Gera o HTML de uma edição específica."""
    date_str   = edition_date.strftime("%d de %B de %Y").lower()
    date_label = edition_date.strftime("%d %b %Y")
    filename   = edition_date.strftime("%Y-%m-%d") + ".html"
    cards      = "\n".join(render_article_card(a, i) for i, a in enumerate(articles))
    n          = len(articles)

    # links para outras edições
    archive_links = ""
    for ed in sorted(all_editions, reverse=True):
        if ed["date"] == edition_date:
            continue
        href = ed["filename"]
        archive_links += f"""
        <a class="archive-card" href="{href}">
          <div class="archive-date">{ed["date_label"]}</div>
          <div class="archive-label">Edição {ed["date"].strftime("%d/%m")}</div>
          <div class="archive-count">{ed["count"]} artigos</div>
        </a>"""

    archive_section = ""
    if archive_links:
        archive_section = f"""
        <hr class="divider">
        <div class="section-head">
          <h2>Edições anteriores</h2>
        </div>
        <div class="archive-grid">{archive_links}</div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ClinicaUpdate — {date_label}</title>
<style>{CSS}</style>
</head>
<body>
<div class="masthead">
  <div class="masthead-left">
    <div class="pub-name">Clínica<span>Update</span></div>
    <div class="pub-sub">// curadoria semanal em medicina interna</div>
  </div>
  <div class="masthead-right">
    <div class="meta-item"><strong>Edição</strong>&nbsp; {date_label}</div>
    <div class="meta-item"><strong>Fontes</strong>&nbsp; PubMed · NEJM · Lancet · JAMA</div>
    <div class="meta-item"><strong>Período</strong>&nbsp; últimos 90 dias</div>
  </div>
</div>
<div class="page">
  <div class="section-head">
    <h2>Curadoria da semana</h2>
    <span class="count">{n} artigos selecionados · {date_str}</span>
  </div>
  {cards}
  {archive_section}
</div>
<div class="footer">
  <div>© ClinicaUpdate · gerado automaticamente via PubMed E-utilities</div>
  <div><a href="https://pubmed.ncbi.nlm.nih.gov" target="_blank">PubMed</a> · atualizado toda segunda-feira</div>
</div>
</body>
</html>"""


def build_index_html(latest_articles, edition_date, all_editions):
    """index.html sempre aponta para a edição mais recente."""
    return build_edition_html(latest_articles, edition_date, all_editions)


def load_editions_manifest(docs_dir):
    """Lê o manifest de edições anteriores."""
    manifest_path = docs_dir / "editions.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text())
        except Exception:
            pass
    return []


def save_editions_manifest(docs_dir, editions):
    manifest_path = docs_dir / "editions.json"
    manifest_path.write_text(json.dumps(editions, indent=2, default=str))


def main():
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    today = datetime.now()
    print(f"[ClinicaUpdate] Gerando edição de {today.strftime('%d/%m/%Y')}...")
    print(f"[ClinicaUpdate] Buscando artigos no PubMed (últimos 90 dias)...")

    articles = collect_articles()
    print(f"[ClinicaUpdate] {len(articles)} artigos selecionados.")

    if not articles:
        print("[ClinicaUpdate] Nenhum artigo encontrado. Abortando.")
        return

    # carrega edições anteriores
    manifest = load_editions_manifest(docs_dir)
    edition_filename = today.strftime("%Y-%m-%d") + ".html"
    edition_date_label = today.strftime("%d %b %Y")

    # adiciona edição atual ao manifest (se ainda não existir)
    existing = [e for e in manifest if e["filename"] == edition_filename]
    if not existing:
        manifest.append({
            "filename": edition_filename,
            "date": today.strftime("%Y-%m-%d"),
            "date_label": edition_date_label,
            "count": len(articles),
        })
    save_editions_manifest(docs_dir, manifest)

    # monta lista de edições com objetos datetime para o template
    all_editions = []
    for e in manifest:
        all_editions.append({
            "filename": e["filename"],
            "date": datetime.strptime(e["date"], "%Y-%m-%d"),
            "date_label": e["date_label"],
            "count": e["count"],
        })

    # gera HTML da edição desta semana
    edition_html = build_edition_html(articles, today, all_editions)
    (docs_dir / edition_filename).write_text(edition_html, encoding="utf-8")
    print(f"[ClinicaUpdate] Edição salva: docs/{edition_filename}")

    # atualiza index.html
    (docs_dir / "index.html").write_text(edition_html, encoding="utf-8")
    print("[ClinicaUpdate] index.html atualizado.")
    print("[ClinicaUpdate] Concluído.")


if __name__ == "__main__":
    main()
