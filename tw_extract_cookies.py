#!/usr/bin/env python3
"""
Extractor de tweets/articles de X usando cookies exportadas manualmente.

SETUP (una sola vez):
  pip install playwright
  python -m playwright install chromium

  1) Instalá la extensión "Cookie-Editor" en Chrome.
  2) Entrá a x.com logueado.
  3) Abrí la extensión -> Export -> "Export as JSON" -> pegá el contenido
     en un archivo llamado cookies.json (en la misma carpeta del script).

USO:
  python tw_extract.py <url> [--pdf] [--out carpeta] [--cookies cookies.json]

IMPORTANTE DE SEGURIDAD:
  El archivo cookies.json contiene tu auth_token: es equivalente a tu
  contraseña + sesión activa. No lo compartas ni lo subas a ningún lado.
  Cuando termines de usarlo, podés borrarlo y/o cerrar sesión en todos los
  dispositivos desde X -> Configuración -> Seguridad -> Sesiones activas,
  para invalidarlo.
"""
import sys, os, re, json, argparse, time
from pathlib import Path

def load_cookies(cookies_path):
    path = Path(cookies_path)
    if not path.exists():
        print(f"[ERROR] No encontré {cookies_path}. Exportá tus cookies con Cookie-Editor primero (ver docstring).")
        sys.exit(1)
    raw = json.loads(path.read_text(encoding="utf-8"))

    samesite_map = {"no_restriction": "None", "lax": "Lax", "strict": "Strict",
                    None: "Lax", "unspecified": "Lax"}
    cookies = []
    for c in raw:
        cookies.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "expires": c.get("expirationDate", -1) or -1,
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", True),
            "sameSite": samesite_map.get(c.get("sameSite"), "Lax"),
        })
    return cookies

def download(url, folder, name, request_ctx):
    folder.mkdir(parents=True, exist_ok=True)
    ext = url.split("?")[0].split(".")[-1][:4] or "jpg"
    path = folder / f"{name}.{ext}"
    resp = request_ctx.get(url)
    path.write_bytes(resp.body())
    return path

def extract_tweet(page):
    tid = re.search(r"status/(\d+)", page.url).group(1)
    author = page.query_selector('[data-testid="User-Name"]')
    text_el = page.query_selector('[data-testid="tweetText"]')
    text = text_el.inner_text() if text_el else ""
    segments = []
    if text:
        segments.append({"type": "text", "text": text})
    for img in page.query_selector_all('[data-testid="tweetPhoto"] img'):
        src = img.get_attribute("src")
        if src:
            segments.append({"type": "img", "src": src.split("&name=")[0] + "&name=orig"})
    return {
        "id": tid,
        "title": f"Tweet {tid}",
        "author": author.inner_text().replace("\n", " ") if author else "?",
        "segments": segments,
    }

def extract_article(page, forced_aid=None):
    if forced_aid:
        aid = forced_aid
    else:
        m = re.search(r"article/(\d+)", page.url)
        aid = m.group(1) if m else f"article_{int(time.time())}"
    title_el = page.query_selector("h1")
    title = title_el.inner_text() if title_el else f"Article {aid}"
    content_el = page.query_selector("div[class*='public-DraftEditor-content'], article")

    # recorremos el contenido en el orden real del DOM: título/párrafo/imagen tal
    # como aparecen, en vez de juntar todo el texto y las imágenes por separado.
    segments = []
    if content_el:
        segments = content_el.evaluate("""
            (root) => {
                const sel = 'h1,h2,h3,h4,p,li,blockquote,div[data-block="true"],img';
                const nodes = Array.from(root.querySelectorAll(sel));
                const out = [];
                for (const el of nodes) {
                    if (el.tagName === 'IMG') {
                        if (!el.src || el.src.includes('profile_images')) continue;
                        out.push({type: 'img', src: el.src});
                        continue;
                    }
                    // saltar bloques que solo envuelven a otro bloque ya listado
                    // (evita duplicar texto de contenedores padres)
                    const hasNestedBlock = el.querySelector('h1,h2,h3,h4,p,li,blockquote,div[data-block="true"]');
                    if (hasNestedBlock) continue;
                    const text = el.innerText.trim();
                    if (!text) continue;
                    const level = {H1:1, H2:2, H3:3, H4:4}[el.tagName] || 0;
                    out.push({type: level ? 'heading' : 'text', level, text});
                }
                return out;
            }
        """)

    author_el = page.query_selector('[data-testid="User-Name"]')
    return {
        "id": aid,
        "title": title,
        "author": author_el.inner_text().replace("\n", " ") if author_el else "?",
        "segments": segments,
    }

def extract(url, out_dir, cookies_path):
    from playwright.sync_api import sync_playwright
    is_article = "/article/" in url
    cookies = load_cookies(cookies_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900},
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(
                '[data-testid="tweetText"], article, [data-testid="tweetPhoto"], a[href*="/article/"]',
                timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        forced_aid = None
        if not is_article:
            article_link = page.query_selector('a[href*="/article/"]')
            if article_link:
                href = article_link.get_attribute("href")
                m = re.search(r"article/(\d+)", href)
                forced_aid = m.group(1) if m else None
                print("[*] Detecté un X Article embebido, haciendo click para entrar...")
                article_link.scroll_into_view_if_needed()
                article_link.click()
                is_article = True

        if is_article:
            # reintentar hasta que el título del artículo realmente tenga texto
            # (a veces X muestra "Something went wrong" un instante y se recupera solo)
            loaded_ok = False
            for attempt in range(5):
                page.wait_for_timeout(2000)
                h1 = page.query_selector("h1")
                h1_text = (h1.inner_text().strip() if h1 else "")
                if h1_text and "went wrong" not in h1_text.lower():
                    loaded_ok = True
                    break
                print(f"[*] Artículo aún no cargó bien (intento {attempt+1}/5), reintentando...")
                retry_btn = page.query_selector('text="Try again"') or page.query_selector('text="Refresh"')
                if retry_btn:
                    retry_btn.click()
                else:
                    page.reload(wait_until="domcontentloaded")
            if not loaded_ok:
                print("[!] No pude cargar el artículo después de varios intentos.")

        data = extract_article(page, forced_aid) if is_article else extract_tweet(page)

        img_dir = out_dir / f"{data['id']}_media"
        body_parts = []
        img_i = 0
        for seg in data["segments"]:
            if seg["type"] == "img":
                try:
                    p_img = download(seg["src"], img_dir, f"img_{img_i}", ctx.request)
                    body_parts.append(f"![img_{img_i}]({p_img.relative_to(out_dir)})")
                except Exception as e:
                    body_parts.append(f"<!-- error img: {e} -->")
                img_i += 1
            elif seg["type"] == "heading":
                body_parts.append(f"{'#' * min(seg.get('level', 2) + 1, 6)} {seg['text']}")
            else:
                body_parts.append(seg["text"])

        browser.close()

    body = "\n\n".join(body_parts)
    md = f"""# {data['title']}

**Autor:** {data['author']}
**URL:** {url}

---

{body}
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{data['id']}.md"
    md_path.write_text(md, encoding="utf-8")
    return md_path

def md_to_pdf(md_path):
    try:
        from markdown_pdf import MarkdownPdf, Section
    except ImportError:
        os.system(f"{sys.executable} -m pip install markdown-pdf -q")
        from markdown_pdf import MarkdownPdf, Section
    pdf = MarkdownPdf()
    pdf.add_section(Section(md_path.read_text(encoding="utf-8"), root=str(md_path.parent)))
    out = md_path.with_suffix(".pdf")
    pdf.save(str(out))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--cookies", default="cookies.json")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--out", default="./tweets_out")
    args = ap.parse_args()

    out_dir = Path(args.out)
    md = extract(args.target, out_dir, args.cookies)
    print(f"[OK] Markdown: {md}")
    if args.pdf:
        print(f"[OK] PDF: {md_to_pdf(md)}")

if __name__ == "__main__":
    main()