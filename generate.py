#!/usr/bin/env python3
"""
ClinicaUpdate — gerador semanal automático
Busca artigos e guidelines no PubMed e gera HTML com curadoria da semana.
"""

import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

# ── tópicos de curadoria ─────────────────────────────────────────────────────
TOPICS = [
    ("sepsis management treatment", "Sepse"),
    ("heart failure randomized trial", "ICC"),
    ("community acquired pneumonia antibiotic", "Pneumonia"),
    ("acute kidney injury", "LRA"),
    ("venous thromboembolism pulmonary embolism", "TEP / TVP"),
    ("hypertension treatment randomized", "Hipertensão"),
    ("atrial fibrillation management", "Fibrilação Atrial"),
    ("type 2 diabetes mellitus treatment", "Diabetes tipo 2"),
    ("acute pancreatitis management", "Pancreatite"),
    ("liver cirrhosis complication", "Cirrose"),
]

GUIDELINE_TOPICS = [
    ("clinical practice guideline internal medicine 2025", "Clínica Médica"),
    ("guidelines sepsis management 2025", "Sepse"),
    ("guidelines heart failure 2025", "ICC"),
    ("guidelines hypertension 2025", "Hipertensão"),
    ("guidelines diabetes mellitus 2025", "Diabetes"),
    ("guidelines anticoagulation atrial fibrillation 2025", "Fibrilação Atrial"),
    ("guidelines venous thromboembolism 2025", "TEP / TVP"),
]

PREFERRED_PUBTYPES = [
    "randomized controlled trial",
    "meta-analysis",
    "systematic review",
    "clinical trial, phase iii",
    "clinical trial, phase ii",
    "guideline",
    "practice guideline",
]

DATE_FROM     = (datetime.now() - timedelta(days=90)).strftime("%Y/%m/%d")
DATE_TO       = datetime.now().strftime("%Y/%m/%d")
MAX_PER_TOPIC = 3
BASE_URL      = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# ── busca PubMed ─────────────────────────────────────────────────────────────

def fetch(url, retries=3, delay=1.5):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                print(f"  [erro] {e}")
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


def search_guideline_pmids(query, max_results=3):
    q = urllib.parse.quote(
        f'({query})[Title/Abstract] AND ("guideline"[Publication Type] OR "practice guideline"[Publication Type])'
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
            parts = article.findall(".//AbstractText")
            abs_parts = []
            for p in parts:
                label = p.get("Label", "")
                text = (p.text or "").strip()
                if text:
                    abs_parts.append(f"{label}: {text}" if label else text)
            if abs_parts:
                abs_map[pmid] = " ".join(abs_parts)
            for aid in article.findall(".//ArticleId"):
                if aid.get("IdType") == "doi" and aid.text:
                    doi_map[pmid] = aid.text.strip()
    except Exception as e:
        print(f"  [erro XML] {e}")
    return abs_map, doi_map


def score_article(summary, pubtype_list):
    score = 0
    types_lower = [t.lower() for t in pubtype_list]
    for i, pt in enumerate(PREFERRED_PUBTYPES):
        if pt in types_lower:
            score += (len(PREFERRED_PUBTYPES) - i) * 10
            break
    journal = (summary.get("fulljournalname") or "").lower()
    HIGH_IMPACT = ["new england journal", "lancet", "jama", "british medical journal",
                   "annals of internal medicine", "nature medicine", "bmj",
                   "chest", "circulation", "european heart journal"]
    if any(j in journal for j in HIGH_IMPACT):
        score += 20
    return score


def collect_articles():
    seen_pmids = set()
    articles = []
    for query, label in TOPICS:
        print(f"  → artigos: {label}")
        pmids = search_pmids(query, DATE_FROM, DATE_TO, max_results=MAX_PER_TOPIC + 2)
        pmids = [p for p in pmids if p not in seen_pmids]
        if not pmids:
            continue
        summaries = fetch_summaries(pmids)
        abs_map, doi_map = fetch_abstracts_and_dois(pmids)
        time.sleep(0.4)
        for pmid in pmids:
            s = summaries.get(pmid)
            if not s or pmid == "uids":
                continue
            abstract = abs_map.get(pmid, "")
            if not abstract:
                continue
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
    articles.sort(key=lambda a: a["score"], reverse=True)
    return articles[:8]


def collect_guidelines():
    seen_pmids = set()
    guidelines = []
    for query, label in GUIDELINE_TOPICS:
        print(f"  → guidelines: {label}")
        pmids = search_guideline_pmids(query, max_results=2)
        pmids = [p for p in pmids if p not in seen_pmids]
        if not pmids:
            continue
        summaries = fetch_summaries(pmids)
        abs_map, doi_map = fetch_abstracts_and_dois(pmids)
        time.sleep(0.4)
        for pmid in pmids:
            s = summaries.get(pmid)
            if not s or pmid == "uids":
                continue
            abstract = abs_map.get(pmid, "")
            guidelines.append({
                "pmid": pmid,
                "title": s.get("title", "").rstrip("."),
                "journal": s.get("fulljournalname") or s.get("source", ""),
                "pub_date": s.get("pubdate", ""),
                "authors": s.get("authors", []),
                "abstract": abstract,
                "pubtype": s.get("pubtype", []),
                "doi": doi_map.get(pmid, ""),
                "topic_label": label,
            })
            seen_pmids.add(pmid)
    return guidelines[:6]


# ── helpers HTML ──────────────────────────────────────────────────────────────

def tipo_badge(pubtypes):
    t = " ".join(pubtypes).lower()
    if "randomized" in t or "phase iii" in t:
        return '<span class="badge badge-rct">Ensaio Clínico</span>'
    if "meta-analysis" in t:
        return '<span class="badge badge-meta">Meta-análise</span>'
    if "systematic review" in t:
        return '<span class="badge badge-meta">Revisão Sistemática</span>'
    if "guideline" in t or "practice guideline" in t:
        return '<span class="badge badge-guide">Diretriz</span>'
    if "review" in t:
        return '<span class="badge badge-review">Revisão</span>'
    return '<span class="badge badge-obs">Estudo</span>'


def fmt_authors(authors):
    names = [a.get("name", "") for a in authors[:3] if a.get("name")]
    result = ", ".join(names)
    return result + " et al." if len(authors) > 3 else result


def article_link(art):
    if art.get("doi"):
        return f"https://doi.org/{art['doi']}"
    return f"https://pubmed.ncbi.nlm.nih.gov/{art['pmid']}"


def resumo_curto(abstract):
    """Retorna no máximo ~600 caracteres para leitura rápida (~2 min)."""
    if len(abstract) <= 600:
        return abstract
    # tenta cortar em frase completa
    trunc = abstract[:600]
    last_period = trunc.rfind(". ")
    if last_period > 300:
        return trunc[:last_period + 1]
    return trunc + "…"


def render_card(art):
    badge   = tipo_badge(art["pubtype"])
    journal = art["journal"][:50] + "…" if len(art["journal"]) > 50 else art["journal"]
    resumo  = resumo_curto(art["abstract"]) if art["abstract"] else "Resumo não disponível para este artigo."
    authors = fmt_authors(art["authors"])
    link    = article_link(art)
    label   = art.get("topic_label", "")
    date    = art.get("pub_date", "")

    return f"""
<div class="card">
  <div class="card-meta">
    {badge}
    {f'<span class="badge badge-topic">{label}</span>' if label else ''}
    {f'<span class="card-date">{date}</span>' if date else ''}
  </div>
  <h3 class="card-title">{art["title"]}</h3>
  <p class="card-journal">{journal}</p>
  <p class="card-resumo">{resumo}</p>
  <div class="card-footer">
    <a class="card-link" href="{link}" target="_blank">Ler artigo completo →</a>
    <span class="card-authors">{authors}</span>
  </div>
</div>"""


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Lora:ital,wght@0,400;0,600;1,400&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #f5f5f0;
  --surface: #ffffff;
  --border: #e0ddd6;
  --text: #1c1c1e;
  --text2: #4a4a4f;
  --text3: #8a8a8f;
  --red: #c0392b;
  --blue: #1a4a8f;
  --green: #1a6b3c;
  --gold: #8a6000;
  --purple: #5a1a8f;
}

html { scroll-behavior: smooth; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', sans-serif;
  font-size: 16px;
  line-height: 1.65;
}

/* cabeçalho */
.cabecalho {
  background: var(--text);
  color: #fff;
  padding: 28px 32px 24px;
  border-bottom: 4px solid var(--red);
}
.cabecalho-inner {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 12px;
}
.logo {
  font-family: 'Lora', serif;
  font-size: 2.2rem;
  font-weight: 600;
  color: #fff;
  line-height: 1;
}
.logo span { color: #ff7c7c; }
.logo-sub {
  font-size: 0.78rem;
  color: #aaa;
  margin-top: 5px;
  letter-spacing: 0.04em;
}
.cabecalho-info {
  text-align: right;
  font-size: 0.8rem;
  color: #aaa;
  line-height: 1.8;
}
.cabecalho-info strong { color: #ddd; }

/* abas */
.abas {
  background: #fff;
  border-bottom: 1px solid var(--border);
  padding: 0 32px;
  display: flex;
  gap: 0;
  overflow-x: auto;
  position: sticky;
  top: 0;
  z-index: 10;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.aba {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--text3);
  padding: 14px 20px;
  cursor: pointer;
  border-bottom: 3px solid transparent;
  white-space: nowrap;
  transition: color 0.15s, border-color 0.15s;
  text-decoration: none;
}
.aba:hover { color: var(--red); }
.aba.ativa { color: var(--red); border-bottom-color: var(--red); }

/* página */
.pagina {
  max-width: 860px;
  margin: 0 auto;
  padding: 36px 24px 80px;
}

/* aviso leitura rápida */
.aviso-leitura {
  background: #fff;
  border: 1px solid var(--border);
  border-left: 4px solid var(--green);
  padding: 13px 18px;
  font-size: 0.84rem;
  color: var(--text2);
  margin-bottom: 28px;
  border-radius: 2px;
}
.aviso-leitura strong { color: var(--green); }

/* seção */
.secao { display: none; }
.secao.ativa { display: block; }

.secao-titulo {
  display: flex;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 22px;
  padding-bottom: 10px;
  border-bottom: 2px solid var(--text);
}
.secao-titulo h2 {
  font-family: 'Lora', serif;
  font-size: 1.4rem;
  font-weight: 600;
}
.secao-titulo .qtd {
  font-size: 0.78rem;
  color: var(--text3);
}

/* card */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 4px solid transparent;
  padding: 22px 24px;
  margin-bottom: 14px;
  transition: border-left-color 0.2s, box-shadow 0.2s;
  border-radius: 2px;
}
.card:hover {
  border-left-color: var(--red);
  box-shadow: 0 3px 16px rgba(0,0,0,0.07);
}

.card-meta {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 10px;
}

.badge {
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 3px 9px;
  border-radius: 3px;
}
.badge-rct    { background: #eaf0ff; color: #1a4a8f; border: 1px solid #c0d0ff; }
.badge-meta   { background: #eafaf0; color: #1a6b3c; border: 1px solid #a0ddb8; }
.badge-guide  { background: #f5eaff; color: #5a1a8f; border: 1px solid #d0a0ff; }
.badge-review { background: #fff8e0; color: #7a5000; border: 1px solid #f0d880; }
.badge-obs    { background: #f0f0f0; color: #4a4a4f; border: 1px solid #d8d8d8; }
.badge-topic  { background: #fff0ea; color: #8f3a1a; border: 1px solid #ffcdb0; }

.card-date {
  font-size: 0.75rem;
  color: var(--text3);
  margin-left: 4px;
}

.card-title {
  font-family: 'Lora', serif;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text);
  line-height: 1.4;
  margin-bottom: 4px;
}

.card-journal {
  font-size: 0.78rem;
  color: var(--text3);
  font-style: italic;
  margin-bottom: 11px;
}

.card-resumo {
  font-size: 0.9rem;
  line-height: 1.75;
  color: var(--text2);
  margin-bottom: 14px;
}

.card-footer {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.card-link {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--blue);
  text-decoration: none;
  border-bottom: 1px solid currentColor;
  padding-bottom: 1px;
  transition: color 0.15s;
}
.card-link:hover { color: var(--red); }

.card-authors {
  font-size: 0.75rem;
  color: var(--text3);
  font-style: italic;
  flex: 1;
}

/* arquivo */
.arquivo-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  margin-top: 6px;
}
.arquivo-card {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 14px 16px;
  text-decoration: none;
  color: var(--text);
  transition: border-color 0.15s, box-shadow 0.15s;
  border-radius: 2px;
  display: block;
}
.arquivo-card:hover {
  border-color: var(--red);
  box-shadow: 0 2px 10px rgba(0,0,0,0.06);
}
.arquivo-data {
  font-size: 0.72rem;
  color: var(--text3);
  margin-bottom: 4px;
}
.arquivo-label {
  font-family: 'Lora', serif;
  font-size: 0.95rem;
  font-weight: 600;
}
.arquivo-qtd {
  font-size: 0.7rem;
  color: var(--text3);
  margin-top: 4px;
}

.divisor {
  border: none;
  border-top: 1px solid var(--border);
  margin: 32px 0;
}

/* rodapé */
.rodape {
  background: var(--text);
  color: #555;
  padding: 20px 32px;
  font-size: 0.75rem;
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.rodape a { color: #888; }

@media (max-width: 600px) {
  .cabecalho { padding: 20px 18px; }
  .cabecalho-inner { flex-direction: column; align-items: flex-start; }
  .cabecalho-info { text-align: left; }
  .abas { padding: 0 14px; }
  .pagina { padding: 24px 14px 60px; }
  .card { padding: 16px 14px; }
  .logo { font-size: 1.7rem; }
}
"""


# ── HTML completo ─────────────────────────────────────────────────────────────

def build_html(articles, guidelines, edition_date, all_editions):
    date_label   = edition_date.strftime("%d/%m/%Y")
    date_extenso = edition_date.strftime("%-d de %B de %Y")
    filename     = edition_date.strftime("%Y-%m-%d") + ".html"
    n_art        = len(articles)
    n_guide      = len(guidelines)

    cards_artigos    = "\n".join(render_card(a) for a in articles)
    cards_guidelines = "\n".join(render_card(g) for g in guidelines)

    # links arquivo
    arquivo_links = ""
    for ed in sorted(all_editions, key=lambda e: e["date"], reverse=True):
        if ed["date"] == edition_date:
            continue
        arquivo_links += f"""
        <a class="arquivo-card" href="{ed['filename']}">
          <div class="arquivo-data">{ed['date_label']}</div>
          <div class="arquivo-label">Edição {ed['date'].strftime('%d/%m')}</div>
          <div class="arquivo-qtd">{ed['count']} artigos</div>
        </a>"""

    arquivo_section = ""
    if arquivo_links:
        arquivo_section = f"""
        <hr class="divisor">
        <div class="secao-titulo">
          <h2>Edições anteriores</h2>
        </div>
        <div class="arquivo-grid">{arquivo_links}</div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ClinicaUpdate — {date_label}</title>
  <style>{CSS}</style>
</head>
<body>

<div class="cabecalho">
  <div class="cabecalho-inner">
    <div>
      <div class="logo">Clínica<span>Update</span></div>
      <div class="logo-sub">Curadoria semanal em medicina interna</div>
    </div>
    <div class="cabecalho-info">
      <strong>Edição de {date_extenso}</strong><br>
      Fonte: PubMed · NEJM · Lancet · JAMA · BMJ<br>
      Atualizado toda segunda-feira
    </div>
  </div>
</div>

<div class="abas">
  <a class="aba ativa" onclick="mostrarAba('artigos', this)" href="#">📄 Artigos da semana</a>
  <a class="aba" onclick="mostrarAba('diretrizes', this)" href="#">📋 Diretrizes recentes</a>
  <a class="aba" onclick="mostrarAba('arquivo', this)" href="#">🗂 Arquivo</a>
</div>

<div class="pagina">

  <div class="aviso-leitura">
    <strong>Leitura rápida:</strong> cada resumo foi pensado para ser lido em até 5 minutos. O link ao final leva ao artigo completo no PubMed.
  </div>

  <!-- ABA: ARTIGOS -->
  <div id="aba-artigos" class="secao ativa">
    <div class="secao-titulo">
      <h2>Artigos da semana</h2>
      <span class="qtd">{n_art} artigos selecionados · {date_extenso}</span>
    </div>
    {cards_artigos if cards_artigos else '<p style="color:var(--text3);font-size:.9rem;">Nenhum artigo encontrado esta semana.</p>'}
  </div>

  <!-- ABA: DIRETRIZES -->
  <div id="aba-diretrizes" class="secao">
    <div class="secao-titulo">
      <h2>Diretrizes recentes</h2>
      <span class="qtd">{n_guide} diretrizes encontradas</span>
    </div>
    <p style="font-size:.87rem;color:var(--text2);margin-bottom:22px;line-height:1.7;">
      Seleção de diretrizes e recomendações clínicas publicadas recentemente em medicina interna. Clique em "Ler artigo completo" para acessar o texto original no PubMed.
    </p>
    {cards_guidelines if cards_guidelines else '<p style="color:var(--text3);font-size:.9rem;">Nenhuma diretriz encontrada esta semana.</p>'}
  </div>

  <!-- ABA: ARQUIVO -->
  <div id="aba-arquivo" class="secao">
    <div class="secao-titulo">
      <h2>Arquivo de edições</h2>
      <span class="qtd">histórico completo</span>
    </div>
    {arquivo_links if arquivo_links else '<p style="color:var(--text3);font-size:.9rem;">Esta é a primeira edição — o histórico aparecerá aqui nas próximas semanas.</p>'}
  </div>

</div>

<div class="rodape">
  <div>© ClinicaUpdate · gerado automaticamente via PubMed</div>
  <div><a href="https://pubmed.ncbi.nlm.nih.gov" target="_blank">PubMed</a> · atualizado toda segunda-feira às 3h</div>
</div>

<script>
function mostrarAba(id, el) {
  document.querySelectorAll('.secao').forEach(s => s.classList.remove('ativa'));
  document.querySelectorAll('.aba').forEach(a => a.classList.remove('ativa'));
  document.getElementById('aba-' + id).classList.add('ativa');
  el.classList.add('ativa');
  return false;
}
</script>

</body>
</html>"""


# ── main ──────────────────────────────────────────────────────────────────────

def load_manifest(docs_dir):
    p = docs_dir / "editions.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return []


def save_manifest(docs_dir, editions):
    (docs_dir / "editions.json").write_text(json.dumps(editions, indent=2, default=str))


def main():
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    today = datetime.now()
    print(f"[ClinicaUpdate] Edição de {today.strftime('%d/%m/%Y')}")
    print("[ClinicaUpdate] Buscando artigos...")
    articles = collect_articles()
    print(f"[ClinicaUpdate] {len(articles)} artigos selecionados.")

    print("[ClinicaUpdate] Buscando diretrizes...")
    guidelines = collect_guidelines()
    print(f"[ClinicaUpdate] {len(guidelines)} diretrizes encontradas.")

    if not articles and not guidelines:
        print("[ClinicaUpdate] Nada encontrado. Abortando.")
        return

    manifest = load_manifest(docs_dir)
    edition_filename  = today.strftime("%Y-%m-%d") + ".html"
    edition_date_label = today.strftime("%d/%m/%Y")

    if not any(e["filename"] == edition_filename for e in manifest):
        manifest.append({
            "filename":   edition_filename,
            "date":       today.strftime("%Y-%m-%d"),
            "date_label": edition_date_label,
            "count":      len(articles),
        })
    save_manifest(docs_dir, manifest)

    all_editions = []
    for e in manifest:
        all_editions.append({
            "filename":   e["filename"],
            "date":       datetime.strptime(e["date"], "%Y-%m-%d"),
            "date_label": e["date_label"],
            "count":      e["count"],
        })

    html = build_html(articles, guidelines, today, all_editions)
    (docs_dir / edition_filename).write_text(html, encoding="utf-8")
    (docs_dir / "index.html").write_text(html, encoding="utf-8")
    (docs_dir / ".nojekyll").write_text("")

    print(f"[ClinicaUpdate] Salvo: docs/{edition_filename}")
    print("[ClinicaUpdate] index.html atualizado.")
    print("[ClinicaUpdate] Concluído.")


if __name__ == "__main__":
    main()
