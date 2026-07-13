#!/usr/bin/env python3
"""
Interfaz web simple para tw_extract.py

SETUP:
  pip install flask playwright
  python -m playwright install chromium

USO:
  python app.py
  -> abrí http://localhost:5000 en el navegador
"""
from flask import Flask, request, render_template_string, send_from_directory
from pathlib import Path
import traceback

import tw_extract_cookies as tw

app = Flask(__name__)
OUT_DIR = Path("./tweets_out")

PAGE = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Twitter/X Extractor</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 60px auto; background:#0f1419; color:#e7e9ea; }
  h1 { font-size: 22px; }
  input[type=text] { width: 100%; padding: 12px; font-size: 15px; border-radius: 8px; border: 1px solid #333; background:#16181c; color:#fff; box-sizing:border-box; }
  button { margin-top: 12px; padding: 12px 20px; font-size: 15px; border-radius: 999px; border: none; background:#1d9bf0; color:#fff; cursor:pointer; }
  button:hover { background:#1a8cd8; }
  .result { margin-top: 24px; padding: 16px; background:#16181c; border-radius: 10px; }
  .error { color:#f4212e; white-space: pre-wrap; }
  a.file { display:inline-block; margin-right:12px; margin-top:8px; color:#1d9bf0; text-decoration:none; }
  label.chk { display:block; margin-top:10px; font-size:14px; }
</style>
</head>
<body>
  <h1>🐦 Extractor de Tweets / X Articles</h1>
  <form method="POST">
    <input type="text" name="url" placeholder="https://x.com/usuario/status/12345..." value="{{ url or '' }}" required>
    <label class="chk"><input type="checkbox" name="pdf" {% if pdf %}checked{% endif %}> Generar también PDF</label>
    <button type="submit">Extraer</button>
  </form>

  {% if error %}
    <div class="result error">{{ error }}</div>
  {% endif %}

  {% if md_file %}
    <div class="result">
      ✅ Listo.<br>
      <a class="file" href="/files/{{ md_file }}" target="_blank">📄 Ver Markdown</a>
      {% if pdf_file %}<a class="file" href="/files/{{ pdf_file }}" target="_blank">📕 Ver PDF</a>{% endif %}
    </div>
  {% endif %}
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    url, error, md_file, pdf_file, want_pdf = None, None, None, None, False
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        want_pdf = bool(request.form.get("pdf"))
        try:
            md_path = tw.extract(url, OUT_DIR, "cookies.json")
            md_file = md_path.name
            if want_pdf:
                pdf_path = tw.md_to_pdf(md_path)
                pdf_file = pdf_path.name
        except Exception as e:
            error = f"Error: {e}\n\n{traceback.format_exc()}"
    return render_template_string(PAGE, url=url, error=error,
                                   md_file=md_file, pdf_file=pdf_file, pdf=want_pdf)

@app.route("/files/<path:filename>")
def files(filename):
    return send_from_directory(OUT_DIR.resolve(), filename)

if __name__ == "__main__":
    app.run(debug=False, port=5000)