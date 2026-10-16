#!/usr/bin/env python3
"""
Envia o boletim (docs/boletim.md + docs/painel.png) por e-mail, via SMTP.

Secrets usados (GitHub → Settings → Secrets and variables → Actions):
  SMTP_USUARIO    conta que envia (ex.: ffabioreitz1@gmail.com)
  SMTP_SENHA      senha de app do Gmail (16 letras) — NÃO é a senha normal
  EMAIL_DESTINOS  destinatários separados por vírgula
  SMTP_SERVIDOR   opcional (padrão smtp.gmail.com)
  SMTP_PORTA      opcional (padrão 465, SSL)

Os destinatários vão em cópia oculta (cada um não vê os outros).
Se faltar algum secret, o script só avisa no log e termina sem erro.
"""
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from zoneinfo import ZoneInfo

AQUI = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(AQUI, "docs")
MD = os.path.join(DOCS, "boletim.md")
PNG = os.path.join(DOCS, "painel.png")


def log(*a):
    print("[e-mail]", *a, flush=True)


def html_do_md(md: str) -> str:
    import markdown
    # a imagem vai anexada no próprio e-mail (não depende de baixar do site)
    md = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\(([^)]*)\)",
                r'<a href="\1"><img src="cid:painel" alt="Panorama do rio Itajaí" style="max-width:100%"></a>', md)
    corpo = markdown.markdown(md, extensions=["tables"])
    return f"""<!doctype html><html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222;max-width:760px">
<style>table{{border-collapse:collapse;margin:8px 0}}th,td{{border:1px solid #ccc;padding:4px 8px}}th{{background:#f2f2f2}}
blockquote{{border-left:4px solid #c0392b;margin:8px 0;padding:4px 10px;background:#fdf2f2}}</style>
{corpo}
<p style="color:#888;font-size:11px">Mensagem automática do robô de alerta de cheias do Vale do Itajaí. Não responda este e-mail.</p>
</body></html>"""


def main():
    usuario = os.environ.get("SMTP_USUARIO", "").strip()
    senha = os.environ.get("SMTP_SENHA", "").replace(" ", "").strip()
    destinos = [d.strip() for d in re.split(r"[,;\s]+", os.environ.get("EMAIL_DESTINOS", "")) if d.strip()]
    if not os.path.exists(MD):
        log("sem boletim nesta execução — nada a enviar")
        return
    if not (usuario and senha and destinos):
        log("SMTP_USUARIO / SMTP_SENHA / EMAIL_DESTINOS não configurados — e-mail direto não enviado")
        return

    md = open(MD, encoding="utf-8").read()
    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    titulo = re.search(r"^###\s*(.+)$", md, re.M)
    assunto = f"Boletim rio Itajaí {agora:%d/%m %Hh%M}" + (f" — {re.sub(r'[*_]', '', titulo.group(1)).strip()}" if titulo else "")

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr(("Alerta de Cheias Itajaí", usuario))
    msg["To"] = usuario
    msg["Bcc"] = ", ".join(destinos)
    texto = re.sub(r"<[^>]+>", "", md)
    msg.set_content(texto)
    msg.add_alternative(html_do_md(md), subtype="html")
    if os.path.exists(PNG):
        msg.get_payload()[1].add_related(open(PNG, "rb").read(), "image", "png", cid="<painel>")

    servidor = os.environ.get("SMTP_SERVIDOR") or "smtp.gmail.com"
    porta = int(os.environ.get("SMTP_PORTA") or 465)
    try:
        if porta == 465:
            with smtplib.SMTP_SSL(servidor, porta, context=ssl.create_default_context(), timeout=60) as s:
                s.login(usuario, senha)
                s.send_message(msg)
        else:
            with smtplib.SMTP(servidor, porta, timeout=60) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(usuario, senha)
                s.send_message(msg)
    except Exception as e:  # noqa — falha de e-mail não derruba o robô
        log("falhou:", repr(e))
        sys.exit(0)
    log(f"enviado para {len(destinos)} destinatário(s): {assunto}")


if __name__ == "__main__":
    main()
