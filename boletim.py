#!/usr/bin/env python3
"""
Boletim horário do rio Itajaí: cruza Defesa Civil/AlertaBlu (Blumenau), Defesa Civil de SC,
barragens (Oeste, Sul e Norte), ANA (réguas das usinas), previsão do tempo, maré e lua.
Gera o infográfico (docs/painel.png + docs/index.html) e manda o resumo no WhatsApp.
"""
import json, math, os, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

import alerta as A  # funções da ANA e do WhatsApp

TZ, log, norm = A.TZ, A.log, A.norm
AQUI = os.path.dirname(os.path.abspath(__file__))
CFG = A.CFG
B = CFG["boletim"]
CACHE = os.path.join(AQUI, "cache")
ARQ_HIST = os.path.join(CACHE, "historico.json")
DOCS = os.path.join(AQUI, "docs")
DC = "https://defesacivil.blumenau.sc.gov.br/static/data/"
AGORA = datetime.now(TZ)


_CTX = {}


def contexto_aia(host):
    """O site da Defesa Civil de Blumenau não envia o certificado intermediário (navegadores buscam sozinhos).
    Aqui fazemos o mesmo: baixamos o intermediário indicado no próprio certificado (AIA) e verificamos a
    cadeia completa até uma raiz confiável — a verificação continua valendo."""
    if host in _CTX:
        return _CTX[host]
    import ssl
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import AuthorityInformationAccessOID
    folha = x509.load_pem_x509_certificate(ssl.get_server_certificate((host, 443)).encode())
    aia = folha.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value
    url = next(d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS)
    bruto = urllib.request.urlopen(url, timeout=30).read()
    try:
        inter = x509.load_der_x509_certificate(bruto)
    except ValueError:
        inter = x509.load_pem_x509_certificate(bruto)
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=inter.public_bytes(Encoding.PEM).decode())
    _CTX[host] = ctx
    return ctx


def get_json(url, tent=3):
    ctx = None
    for i in range(tent):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "alerta-cheias-itajai/1.0"})
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa
            if "CERTIFICATE_VERIFY_FAILED" in str(e) and ctx is None:
                try:
                    ctx = contexto_aia(urllib.parse.urlparse(url).hostname)
                    continue
                except Exception as e2:  # noqa
                    log("cadeia de certificado não resolvida:", e2)
            log("falha", url.split("?")[0], e)
            time.sleep(4 * (i + 1))
    return None


def int2hex(c):
    return "#%06x" % int(c)


# ------------------------------------------------------------ coleta
def blumenau_oficial():
    j = get_json(DC + "nivel_oficial.json?a=%d" % time.time()) or {}
    serie = []
    for n in j.get("niveis", []):
        try:
            d = datetime.fromisoformat(n["horaLeitura"].replace("Z", "+00:00")).astimezone(TZ)
            serie.append((d, float(n["nivel"])))
        except Exception:  # noqa
            pass
    serie.sort()
    faixas = sorted(({"nivel": c["nivel"], "nome": c["condicao"], "cor": int2hex(c["cor_fundo"])}
                     for c in j.get("condicoes", []) if c.get("tipo") == "nvl"), key=lambda c: c["nivel"])
    return serie, faixas


def faixa_de(nivel, faixas):
    f = faixas[0] if faixas else {"nome": "—", "cor": "#999999", "nivel": 0}
    for c in faixas:
        if nivel >= c["nivel"]:
            f = c
    return f


def leitura_dt(s):
    try:
        return datetime.strptime(s, "%d/%m/%Y - %H:%M").replace(tzinfo=TZ)
    except Exception:  # noqa
        return None


def estacoes_estado():
    """Réguas da Defesa Civil de SC, barragens e chuva acumulada (fonte: painel AlertaBlu)."""
    j = get_json(DC + "chuva.json?q=%d" % time.time()) or []
    rios, barragens, chuvas = [], [], []
    for e in j:
        d = e.get("dados") or {}
        v = d.get("Variance") or {}
        dt = leitura_dt(e.get("horaLeitura") or "")
        recente = dt is not None and AGORA - dt < timedelta(hours=B["dado_velho_h"])
        nome = (e.get("estacao") or "").replace("DCSC ", "").strip()
        if e.get("tipo_estacao") == "brm":
            ref = next((r for k, r in B["barragens_ref"].items() if norm(k) in norm(nome)), {})
            barragens.append({"nome": nome, "mont": d.get("mont"), "jus": d.get("jus"),
                              "abertas": d.get("compA"), "fechadas": d.get("compF"), "ocup": d.get("cap"),
                              "h24": v.get("h24"), "hora": dt, "recente": recente, "ref": ref})
            continue
        if not recente:
            continue
        if e.get("tipo_estacao") == "lnm" and d.get("niv") is not None and nome in B["reguas"]:
            rios.append({"id": e["estacao_id"], "nome": nome, "nivel": float(d["niv"]),
                         "h24": v.get("h24"), "hora": dt})
        if e.get("tipo") == "estado_sc" and v.get("h24") is not None:
            chuvas.append({"nome": nome, "h01": v.get("h01") or 0, "h24": v.get("h24") or 0,
                           "h72": v.get("h72") or 0})
    ordem = {n: i for i, n in enumerate(B["reguas"])}
    rios.sort(key=lambda r: ordem.get(r["nome"], 99))
    return rios, barragens, chuvas


def aviso_blumenau():
    j = get_json(DC + "aviso.json?q=%d" % time.time()) or {}
    import html, re
    txt = re.sub(r"<[^>]+>", " ", html.unescape(j.get("conteudo") or ""))
    txt = " ".join(txt.split())
    corte = txt.find(" Gabriel") if " Gabriel" in txt else txt.find(" Meteorologista")
    return (j.get("titulo") or "").strip(), (txt[:corte] if corte > 0 else txt)[:400]


def previsao():
    pts = B["pontos_previsao"]
    q = {"latitude": ",".join(str(p[1]) for p in pts), "longitude": ",".join(str(p[2]) for p in pts),
         "daily": "precipitation_sum,precipitation_probability_max",
         "hourly": "precipitation,temperature_2m,wind_speed_10m,wind_gusts_10m,wind_direction_10m",
         "timezone": "America/Sao_Paulo", "forecast_days": 4}
    j = get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))
    if not j:
        return None
    if isinstance(j, dict):
        j = [j]
    out = {}
    for (nome, _, _), r in zip(pts, j):
        h = r["hourly"]
        ts = [datetime.fromisoformat(t).replace(tzinfo=TZ) for t in h["time"]]
        prox = [i for i, t in enumerate(ts) if AGORA <= t < AGORA + timedelta(hours=72)]
        acum = lambda n: round(sum(h["precipitation"][i] or 0 for i in prox[:n]), 1)  # noqa
        out[nome] = {"p24": acum(24), "p48": acum(48), "p72": acum(72),
                     "prob": max(r["daily"]["precipitation_probability_max"][:2] or [0]),
                     "horas": [(ts[i], h["temperature_2m"][i], h["precipitation"][i],
                                h["wind_gusts_10m"][i], h["wind_direction_10m"][i]) for i in prox[:24]]}
    return out


def mare():
    lat, lon = B["ponto_mare"]
    q = {"latitude": lat, "longitude": lon, "hourly": "sea_level_height_msl,wave_height",
         "timezone": "America/Sao_Paulo", "past_days": 1, "forecast_days": 3, "cell_selection": "sea"}
    j = get_json("https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode(q))
    if not j or "hourly" not in j:
        return None
    h = j["hourly"]
    pts = [(datetime.fromisoformat(t).replace(tzinfo=TZ), v) for t, v in zip(h["time"], h["sea_level_height_msl"])
           if v is not None]
    ondas = [v for t, v in zip(h["time"], h.get("wave_height") or []) if v is not None]
    ext = []
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1][1], pts[i][1], pts[i + 1][1]
        if b > a and b >= c:
            ext.append(("preamar", *pts[i]))
        elif b < a and b <= c:
            ext.append(("baixa-mar", *pts[i]))
    janela = [p for p in pts if AGORA - timedelta(hours=6) <= p[0] <= AGORA + timedelta(hours=30)]
    prox = [e for e in ext if AGORA <= e[1] <= AGORA + timedelta(hours=26)]
    return {"curva": janela, "proximos": prox, "onda": round(max(ondas[:72]), 1) if ondas else None}


def lua(dt):
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    sin = 29.530588853
    idade = ((dt - ref).total_seconds() / 86400) % sin
    fr = idade / sin
    nomes = [(0.03, "🌑", "Lua nova"), (0.22, "🌒", "Crescente"), (0.28, "🌓", "Quarto crescente"),
             (0.47, "🌔", "Crescente gibosa"), (0.53, "🌕", "Lua cheia"), (0.72, "🌖", "Minguante gibosa"),
             (0.78, "🌗", "Quarto minguante"), (0.97, "🌘", "Minguante"), (1.01, "🌑", "Lua nova")]
    for lim, emo, nome in nomes:
        if fr < lim:
            break
    ilum = round((1 - math.cos(2 * math.pi * fr)) / 2 * 100)
    sizigia = fr < 0.1 or fr > 0.9 or 0.4 < fr < 0.6  # perto de lua nova/cheia: marés de sizígia
    return {"emoji": emo, "nome": nome, "ilum": ilum, "sizigia": sizigia}


def ana_complementar():
    if not (os.environ.get("ANA_ID") and os.environ.get("ANA_SENHA")):
        return [], None
    try:
        tok = A.token_ana()
    except SystemExit as e:
        log("ANA indisponível:", e)
        return [], None
    out = []
    for cod, nome in B["ana_estacoes"].items():
        r = A.resumo_serie(A.serie(tok, cod, "HORA_6"))
        if r:
            out.append({"nome": nome, **r})
    return out, tok


# ------------------------------------------------------------ previsão de nível (Blumenau)
ARQ_CURVA = os.path.join(CACHE, "curva.json")


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def curva_chave(tok):
    """Ajusta Q = a·(h−h0)^b com as medições de descarga da ANA (régua de Blumenau). Guarda por 30 dias."""
    try:
        c = json.load(open(ARQ_CURVA, encoding="utf-8"))
        if AGORA - datetime.fromisoformat(c["quando"]) < timedelta(days=30):
            return c
    except Exception:  # noqa
        pass
    if tok is None:
        return None
    import numpy as np
    itens = A.get(tok, "HidroSerieResumoDescarga/v1", {
        "Código da Estação": B["estacao_curva"], "Tipo Filtro Data": "DATA_LEITURA",
        "Data Inicial (yyyy-MM-dd)": "2000-01-01", "Data Final (yyyy-MM-dd)": AGORA.strftime("%Y-%m-%d")})
    if itens:
        log("resumo de descarga: campos", list(itens[0].keys())[:25])
    pares = []
    for it in itens:
        h = next((_num(v) for k, v in it.items() if "cota" in k.lower() and _num(v) is not None), None)
        q = next((_num(v) for k, v in it.items() if "vazao" in k.lower().replace("ã", "a") and _num(v) is not None), None)
        if h is not None and q and q > 0:
            pares.append((h / 100 if h > 30 else h, q))  # cota em cm → m
    log("pares nível×vazão:", len(pares))
    if len(pares) < 8:
        return None
    hs, qs = np.array([p[0] for p in pares]), np.array([p[1] for p in pares])
    melhor = None
    for h0 in np.linspace(hs.min() - 6, hs.min() - 0.02, 300):
        x, y = np.log(hs - h0), np.log(qs)
        b, la = np.polyfit(x, y, 1)
        r2 = 1 - ((y - (la + b * x)) ** 2).sum() / ((y - y.mean()) ** 2).sum()
        if melhor is None or r2 > melhor[0]:
            melhor = (r2, float(np.exp(la)), float(b), float(h0))
    r2, a, b, h0 = melhor
    log(f"curva-chave: Q = {a:.2f}·(h−{h0:.2f})^{b:.2f}  R²={r2:.3f}  ({len(pares)} medições)")
    if r2 < 0.85:
        return None
    c = {"a": a, "b": b, "h0": h0, "r2": r2, "n": len(pares), "quando": AGORA.isoformat()}
    os.makedirs(CACHE, exist_ok=True)
    json.dump(c, open(ARQ_CURVA, "w"))
    return c


def previsao_nivel(blu_nivel, tok):
    """Vazão prevista pelo GloFAS (Copernicus, via Open-Meteo), ajustada à vazão de hoje e convertida em nível
    pela curva-chave. Estimativa experimental — não substitui a previsão oficial."""
    lat, lon = B["ponto_glofas"]
    vs = "river_discharge,river_discharge_p25,river_discharge_p75,river_discharge_max"
    j = get_json("https://flood-api.open-meteo.com/v1/flood?" + urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon, "daily": vs, "past_days": 2, "forecast_days": 5}))
    if not j or "daily" not in j:
        return None
    d = j["daily"]
    dias = [datetime.fromisoformat(t).date() for t in d["time"]]
    hoje = AGORA.date()
    if hoje not in dias:
        return None
    i0 = dias.index(hoje)
    q0 = d["river_discharge"][i0]
    if not q0:
        return None
    serie = lambda k: d.get(k) or [None] * len(dias)  # noqa
    out = {"dias": dias[i0:i0 + 4], "q": serie("river_discharge")[i0:i0 + 4],
           "q25": serie("river_discharge_p25")[i0:i0 + 4], "q75": serie("river_discharge_p75")[i0:i0 + 4],
           "qmax": serie("river_discharge_max")[i0:i0 + 4], "q0": q0, "modo": "relativo"}
    curva = curva_chave(tok)
    if curva and blu_nivel is not None and blu_nivel > curva["h0"]:
        a, b, h0 = curva["a"], curva["b"], curva["h0"]
        qobs = a * (blu_nivel - h0) ** b
        fator = qobs / q0
        nivel = lambda q: None if q is None else round(h0 + (fator * q / a) ** (1 / b), 2)  # noqa
        out.update(modo="nivel", fator=round(fator, 2), qobs=round(qobs),
                   h=[nivel(q) for q in out["q"]], h25=[nivel(q) for q in out["q25"]],
                   h75=[nivel(q) for q in out["q75"]], hmax=[nivel(q) for q in out["qmax"]])
        out["h"][0] = blu_nivel
    return out


# ------------------------------------------------------------ histórico e tendência
def historico(rios, barragens):
    try:
        hist = json.load(open(ARQ_HIST, encoding="utf-8"))
    except Exception:  # noqa
        hist = {}
    for r in rios:
        s = hist.setdefault(r["nome"], [])
        t = r["hora"].isoformat()
        if not s or s[-1][0] != t:
            s.append([t, r["nivel"]])
        hist[r["nome"]] = s[-72:]
    for r in rios:
        r["delta3h"] = tendencia(hist.get(r["nome"], []), 3)
    comp = hist.setdefault("_comportas", {})
    for b in barragens:
        atual = [b.get("abertas"), b.get("fechadas")]
        b["mudou"] = b["nome"] in comp and comp[b["nome"]] != atual and b.get("recente")
        comp[b["nome"]] = atual
    os.makedirs(CACHE, exist_ok=True)
    json.dump(hist, open(ARQ_HIST, "w", encoding="utf-8"))
    return hist


def tendencia(serie, horas):
    """variação (m) nas últimas `horas` a partir de [[iso, nivel], ...] ou [(dt, nivel)]."""
    if len(serie) < 2:
        return None
    pts = [((datetime.fromisoformat(t) if isinstance(t, str) else t), v) for t, v in serie]
    t1, v1 = pts[-1]
    ant = [v for t, v in pts if t <= t1 - timedelta(hours=horas) + timedelta(minutes=20)]
    return round(v1 - ant[-1], 2) if ant else None


def seta(d):
    if d is None:
        return ""
    return "↑" if d >= 0.05 else ("↓" if d <= -0.05 else "→")


# ------------------------------------------------------------ cenários
def cenarios(blu_nivel, blu_d3, faixa, rios, barr, chuva_obs, prev, mar, moon):
    c = []
    p = prev or {}
    bacia = [v for k, v in p.items() if k != "Itajaí"]
    p24 = round(sum(v["p24"] for v in bacia) / len(bacia), 0) if bacia else None
    p72 = round(sum(v["p72"] for v in bacia) / len(bacia), 0) if bacia else None
    alto = [r for r in rios if r["nome"] in B["alto_vale"] and r.get("delta3h") is not None]
    sobe_alto = [r for r in alto if r["delta3h"] >= 0.3]

    if faixa["nome"] in ("Alerta", "Alerta Máximo"):
        c.append(("🔴", "provável", f"Rio em {faixa['nome'].upper()} em Blumenau — seguir as orientações da Defesa Civil (199/193)."))
    if blu_d3 is not None and blu_d3 >= 0.3:
        c.append(("📈", "em curso", f"Itajaí-Açu subindo em Blumenau ({blu_d3:+.2f} m em 3 h)."))
    if sobe_alto:
        nomes = ", ".join(f"{r['nome']} {r['delta3h']:+.2f} m" for r in sobe_alto)
        c.append(("🌊", "possível", f"Subida no Alto Vale ({nomes}); a onda costuma chegar a Blumenau em 12–24 h."))
    if p72 is not None:
        if p72 >= 100 or (p24 or 0) >= 60:
            c.append(("⛈️", "atenção", f"Chuva volumosa prevista na bacia (~{p24:.0f} mm/24 h, ~{p72:.0f} mm/72 h): "
                                        "elevação significativa do rio é possível em 24–48 h."))
        elif p72 >= 40:
            c.append(("🌧️", "possível", f"Chuva moderada prevista (~{p72:.0f} mm em 72 h): rio pode subir, sem indicação de cheia."))
        else:
            c.append(("🌤️", "provável", f"Pouca chuva prevista (~{p72:.0f} mm em 72 h): rio tende a estabilidade ou descida."))
    mud = [b for b in barr if b.get("mudou")]
    if mud:
        c.append(("🚧", "atenção", "Mudança na operação das comportas: " + ", ".join(
            f"{b['nome']} ({b.get('abertas') or 0} abertas / {b.get('fechadas') or 0} fechadas)" for b in mud) + "."))
    if mar and mar["proximos"]:
        pre = [e for e in mar["proximos"] if e[0] == "preamar"]
        it = (prev or {}).get("Itajaí")
        rajada = max((h[3] or 0) for h in it["horas"]) if it else 0
        if pre and (faixa["nivel"] >= 4 or moon["sizigia"]):
            c.append(("🌗", "atenção", f"Preamar às {pre[0][1]:%H:%M}" + (" em período de sizígia" if moon["sizigia"] else "")
                      + ": escoamento mais lento na foz em Itajaí."))
        if rajada >= B["rajada_porto_kmh"]:
            c.append(("💨", "possível", f"Rajadas de até {rajada:.0f} km/h em Itajaí: possível restrição de manobras na barra."))
    if not c:
        c.append(("✅", "provável", "Sem sinais de mudança relevante nas próximas horas."))
    return c, p24, p72


# ------------------------------------------------------------ infográfico
def infografico(ctx):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    INK, MUTED, GRID, BG = "#1d2433", "#5b6475", "#e3e6ec", "#ffffff"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                         "xtick.color": MUTED, "ytick.color": MUTED})
    fig = plt.figure(figsize=(9, 14), dpi=120, facecolor=BG)
    gs = fig.add_gridspec(6, 2, height_ratios=[0.55, 2.3, 2.15, 1.35, 1.3, 1.5], hspace=0.62, wspace=0.28,
                          left=0.07, right=0.96, top=0.985, bottom=0.03)
    f = ctx["faixa"]

    # cabeçalho
    ax = fig.add_subplot(gs[0, :]); ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, color=f["cor"], clip_on=False))
    claro = f["nome"] in ("Alerta", "Alerta Máximo")
    cor_txt = "#ffffff" if claro else INK
    ax.text(0.02, 0.62, "RIO ITAJAÍ — PANORAMA", fontsize=17, weight="bold", color=cor_txt, transform=ax.transAxes, va="center")
    ax.text(0.02, 0.2, f"{AGORA:%d/%m/%Y %H:%M} · Blumenau: {f['nome'].upper()}", fontsize=11.5, color=cor_txt,
            transform=ax.transAxes, va="center")
    ax.text(0.98, 0.45, f"{ctx['blu_nivel']:.2f} m" if ctx["blu_nivel"] is not None else "—", fontsize=26, weight="bold",
            color=cor_txt, transform=ax.transAxes, ha="right", va="center")

    # Blumenau: observado + estimativa 3 dias
    ax = fig.add_subplot(gs[1, :])
    s = ctx["blu_serie"][-24:]
    pn = ctx.get("prev_nivel")
    if s:
        xs, ys = [p[0] for p in s], [p[1] for p in s]
        fut = []
        if pn and pn["modo"] == "nivel":
            meio = lambda d: datetime(d.year, d.month, d.day, 12, tzinfo=TZ)  # noqa
            fut = [(xs[-1], ys[-1], ys[-1], ys[-1])] + [
                (meio(dd), h, lo if lo is not None else h, hi if hi is not None else h)
                for dd, h, lo, hi in zip(pn["dias"][1:], pn["h"][1:], pn["h25"][1:], pn["h75"][1:]) if h is not None]
        top = max(max(ys + [f[3] for f in fut]) + 1, 5)
        faixas = ctx["faixas"]
        for i, fx in enumerate(faixas):
            sup = faixas[i + 1]["nivel"] if i + 1 < len(faixas) else top + 5
            if fx["nivel"] < top:
                ax.axhspan(fx["nivel"], min(sup, top), color=fx["cor"], alpha=0.16, lw=0)
                ax.text(xs[0], fx["nivel"] + 0.05, f" {fx['nome']} ({fx['nivel']:g} m)", fontsize=8, color=MUTED, va="bottom")
        ax.plot(xs, ys, color="#1f4e9c", lw=2, label="observado (AlertaBlu)")
        ax.plot(xs[-1], ys[-1], "o", ms=8, color="#1f4e9c", mec="white", mew=2)
        ax.annotate(f"agora {ys[-1]:.2f} m", (xs[-1], ys[-1]), xytext=(0, 12), textcoords="offset points", ha="center",
                    fontsize=9.5, weight="bold")
        fim = xs[-1] + timedelta(minutes=30)
        if len(fut) > 1:
            fx_, fy, flo, fhi = zip(*fut)
            ax.fill_between(fx_, flo, fhi, color="#1f4e9c", alpha=0.12, lw=0, label="faixa provável")
            ax.plot(fx_, fy, color="#1f4e9c", lw=2, ls="--", label="estimativa (GloFAS ajustado)")
            for x_, y_ in zip(fx_[1:], fy[1:]):
                ax.plot(x_, y_, "o", ms=6, color="#1f4e9c", mec="white", mew=1.5)
                ax.annotate(f"{x_:%d/%m}\n{y_:.1f} m", (x_, y_), xytext=(0, 9), textcoords="offset points",
                            ha="center", fontsize=8.5)
            fim = fx_[-1] + timedelta(hours=10)
            ax.axvline(xs[-1], color=MUTED, lw=0.8, ls=":")
            ax.legend(loc="lower right", fontsize=8, frameon=True, facecolor="white", edgecolor=GRID)
            ax.xaxis.set_major_locator(mdates.DayLocator(tz=TZ))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m", tz=TZ))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Hh", tz=TZ))
        ax.set_ylim(0, top); ax.set_xlim(xs[0], fim)
    tit = ("Itajaí-Açu em Blumenau — últimas 24 h e estimativa para 3 dias" if pn and pn["modo"] == "nivel"
           else "Itajaí-Açu em Blumenau — nível oficial (AlertaBlu), últimas 24 h")
    ax.set_title(tit, loc="left", fontsize=11, weight="bold")
    ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)

    # réguas
    ax = fig.add_subplot(gs[2, 0]); ax.axis("off")
    ax.set_title("Réguas da bacia (Defesa Civil SC)", loc="left", fontsize=11, weight="bold")
    linhas = ctx["rios"][:10]
    for i, r in enumerate(linhas):
        y = 0.88 - i * (0.88 / max(len(linhas), 1))
        d = r.get("delta3h")
        ax.text(0.0, y, r["nome"], fontsize=9.5, transform=ax.transAxes, va="top")
        ax.text(0.62, y, f"{r['nivel']:.2f} m", fontsize=9.5, weight="bold", transform=ax.transAxes, va="top", ha="right")
        ax.text(0.66, y, seta(d) + ("" if d is None else f" {d:+.2f}"), fontsize=9, transform=ax.transAxes, va="top",
                color=("#b3261e" if (d or 0) >= 0.05 else MUTED))
        ax.text(1.0, y, f"{r['h24'] or 0:.0f} mm", fontsize=8.5, color=MUTED, transform=ax.transAxes, va="top", ha="right")
    ax.text(0.66, 0.985, "Δ 3h", fontsize=7.5, color=MUTED, transform=ax.transAxes, va="top")
    ax.text(1.0, 0.985, "chuva 24h", fontsize=7.5, color=MUTED, transform=ax.transAxes, va="top", ha="right")

    # barragens + ANA
    ax = fig.add_subplot(gs[2, 1]); ax.axis("off")
    ax.set_title("Barragens de contenção", loc="left", fontsize=11, weight="bold")
    y = 0.94
    for b in ctx["barragens"]:
        ref = b.get("ref") or {}
        fech = int(b.get("fechadas") or 0)
        ocup = b.get("ocup")
        ax.text(0, y, ref.get("rotulo", b["nome"]), fontsize=9.5, weight="bold", transform=ax.transAxes, va="top")
        ax.text(1, y, (f"ocupação {ocup:.1f}%" if ocup < 10 else f"ocupação {ocup:.0f}%").replace(".", ",") if ocup is not None else "", fontsize=9.5, weight="bold",
                transform=ax.transAxes, va="top", ha="right", color=("#b3261e" if (ocup or 0) >= 50 else INK))
        y -= 0.1
        mont = b.get("mont")
        linha = f"comportas {int(b.get('abertas') or 0)} ab. / {fech} fech."
        if mont:
            linha += f" · NA {mont:.1f} m"
            if ref.get("vertedouro_m"):
                linha += f" · vertedouro {ref['vertedouro_m']:.0f} m"
        ax.text(0, y, linha, fontsize=8, color=MUTED, transform=ax.transAxes, va="top")
        y -= 0.085
        gat = ref.get("gatilho")
        if gat:
            atual = ctx["gatilhos"].get(b["nome"])
            ax.text(0, y, f"fecha comportas: {gat}" + (f" — agora {atual:.2f} m" if atual is not None else ""),
                    fontsize=8, color=MUTED, transform=ax.transAxes, va="top")
        y -= 0.115
    if ctx["ana"]:
        ax.text(0, y, "Réguas das usinas (ANA)", fontsize=9.5, weight="bold", transform=ax.transAxes, va="top")
        y -= 0.11
        for r in ctx["ana"][:3]:
            ax.text(0, y, f"{r['nome']}: {r['nivel_m']:.2f} m" + ("" if r["taxa_cmh"] is None else f" ({r['taxa_cmh']:+.0f} cm/h)"),
                    fontsize=8.5, color=MUTED, transform=ax.transAxes, va="top")
            y -= 0.1

    # chuva observada x prevista
    ax = fig.add_subplot(gs[3, 0])
    rot = ["obs 72h", "obs 24h", "prev 24h", "prev 48h", "prev 72h"]
    vals = [ctx["chuva_obs72"], ctx["chuva_obs24"], ctx["p24"] or 0, ctx["p48"] or 0, ctx["p72"] or 0]
    cores = ["#8fa3c7", "#8fa3c7", "#1f4e9c", "#1f4e9c", "#1f4e9c"]
    bars = ax.bar(rot, vals, color=cores, width=0.6)
    for bb, v in zip(bars, vals):
        ax.text(bb.get_x() + bb.get_width() / 2, v, f"{v:.0f}", ha="center", va="bottom", fontsize=9)
    ax.set_title("Chuva média na bacia (mm)", loc="left", fontsize=11, weight="bold")
    ax.set_ylim(0, max(vals + [20]) * 1.25)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(axis="x", labelsize=8)

    # maré
    ax = fig.add_subplot(gs[3, 1])
    m, lu = ctx["mare"], ctx["lua"]
    if m and m["curva"]:
        xs, ys = [p[0] for p in m["curva"]], [p[1] for p in m["curva"]]
        ax.plot(xs, ys, color="#0f7c8c", lw=2)
        ax.axvline(AGORA, color=MUTED, lw=0.8, ls=":")
        for tipo, t, v in m["proximos"][:4]:
            ax.plot(t, v, "o", ms=6, color="#0f7c8c", mec="white", mew=1.5)
            ax.annotate(f"{t:%H:%M}", (t, v), xytext=(0, 7 if tipo == "preamar" else -13), textcoords="offset points",
                        ha="center", fontsize=8)
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18], tz=TZ))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Hh", tz=TZ))
        lo, hi = min(ys), max(ys)
        ax.set_ylim(lo - 0.3 * (hi - lo), hi + 0.35 * (hi - lo))
        ax.set_ylabel("m (modelo)")
    ax.set_title(f"Maré na barra de Itajaí · {lu['nome']} {lu['ilum']}%", loc="left", fontsize=11, weight="bold")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", color=GRID, lw=0.8)

    # tempo em Itajaí 24 h
    ax = fig.add_subplot(gs[4, :])
    it = (ctx["prev"] or {}).get("Itajaí")
    if it:
        hs = it["horas"]
        xs = [h[0] for h in hs]
        ax.bar(xs, [h[2] or 0 for h in hs], width=0.035, color="#1f4e9c", label="chuva (mm/h)")
        ax.set_ylim(0, max([h[2] or 0 for h in hs] + [5]) * 1.3)
        ax.set_ylabel("chuva mm/h")
        tmin, tmax = min(h[1] for h in hs), max(h[1] for h in hs)
        raj = max(hs, key=lambda h: h[3] or 0)
        ax.set_title(f"Itajaí — chuva prevista, próximas 24 h · {tmin:.0f}–{tmax:.0f} °C · chance {it['prob']}% · "
                     f"rajada máx. {raj[3] or 0:.0f} km/h às {raj[0]:%Hh}", loc="left", fontsize=10.5, weight="bold")
        ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Hh", tz=TZ))
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)

    # cenários
    ax = fig.add_subplot(gs[5, :]); ax.axis("off")
    ax.set_title("Cenários para as próximas horas", loc="left", fontsize=11, weight="bold")
    y = 0.95
    import textwrap
    for emo, prob, txt in ctx["cenarios"][:5]:
        linhas = textwrap.wrap(txt, 88)
        ax.text(0, y, f"[{prob}]", fontsize=8.5, color=MUTED, transform=ax.transAxes, va="top")
        ax.text(0.11, y, "\n".join(linhas), fontsize=9.5, transform=ax.transAxes, va="top")
        y -= 0.07 + 0.11 * len(linhas)
    ax.text(0, -0.04, "Fontes: Defesa Civil de Blumenau/AlertaBlu, Defesa Civil de SC, ANA, Open-Meteo (previsão e maré por modelo). "
            "Cenários são indicativos — a palavra oficial é da Defesa Civil.", fontsize=7, color=MUTED, transform=ax.transAxes,
            va="top", wrap=True)

    os.makedirs(DOCS, exist_ok=True)
    fig.savefig(os.path.join(DOCS, "painel.png"), facecolor=BG)
    plt.close(fig)


def pagina(ctx, texto):
    url = B["url_pagina"].rstrip("/")
    ts = AGORA.strftime("%Y%m%d%H%M")
    html = f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="900">
<title>Rio Itajaí — panorama {AGORA:%d/%m %H:%M}</title>
<meta property="og:title" content="Rio Itajaí — {ctx['faixa']['nome']} · {ctx['blu_nivel'] or 0:.2f} m em Blumenau">
<meta property="og:description" content="Panorama de {AGORA:%d/%m %H:%M}: réguas, barragens, chuva, maré e cenários.">
<meta property="og:image" content="{url}/painel.png?v={ts}">
<meta property="og:type" content="website">
<style>body{{margin:0;background:#f4f6f9;font-family:system-ui,sans-serif;color:#1d2433}}
main{{max-width:1080px;margin:0 auto;padding:12px}}img{{width:100%;height:auto;border-radius:8px;background:#fff}}
pre{{white-space:pre-wrap;background:#fff;padding:12px;border-radius:8px;font-size:14px}}</style></head>
<body><main><img src="painel.png?v={ts}" alt="Panorama do rio Itajaí"><pre>{texto}</pre></main></body></html>"""
    open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8").write(html)
    open(os.path.join(DOCS, ".nojekyll"), "w").write("")


def br(x, casas=2):
    return f"{x:.{casas}f}".replace(".", ",")


def email_md(c, blu_d3, aviso, titulo_aviso):
    url = B.get("url_pagina", "").rstrip("/")
    f = c["faixa"]
    emo = {"Normalidade": "🟢", "Observação": "🟡", "Atenção": "🟠", "Alerta": "🔴", "Alerta Máximo": "🟣"}.get(f["nome"], "⚪")
    tend = ("subindo" if (blu_d3 or 0) >= 0.05 else "descendo" if (blu_d3 or 0) <= -0.05 else "estável") if blu_d3 is not None else ""
    M = []
    if url:
        M.append(f"[![Panorama do rio Itajaí]({url}/painel.png?v={AGORA:%Y%m%d%H%M})]({url}/)")
        M.append("")
    if c["blu_nivel"] is not None:
        M.append(f"### {emo} Blumenau {br(c['blu_nivel'])} m — {f['nome']}" + (f" · {tend}" if tend else ""))
    pn = c.get("prev_nivel")
    if pn and pn["modo"] == "nivel":
        dias = " · ".join(f"{d:%d/%m} **{br(h, 1)} m**" for d, h in zip(pn["dias"][1:], pn["h"][1:]) if h is not None)
        hi = max((x for x in pn["h75"][1:] if x is not None), default=None)
        M.append(f"**Próximos dias (estimativa):** {dias}" + (f" — faixa provável até {br(hi, 1)} m" if hi else ""))
    elif pn:
        var = [round((q / pn['q0'] - 1) * 100) for q in pn["q"][1:] if q]
        M.append("**Próximos dias (vazão prevista, relativa a hoje):** " + " · ".join(
            f"{d:%d/%m} {v:+d}%" for d, v in zip(pn["dias"][1:], var)))
    M.append(f"**Chuva na bacia:** {br(c['chuva_obs24'], 0)} mm nas últimas 24 h · previsão "
             f"{br(c['p24'] or 0, 0)} / {br(c['p48'] or 0, 0)} / {br(c['p72'] or 0, 0)} mm em 24 / 48 / 72 h")
    M.append("")
    if c["barragens"]:
        M.append("| Barragem | Ocupação | Comportas (abertas / fechadas) | Fecha comportas quando |")
        M.append("|---|---:|:---:|---|")
        for b in c["barragens"]:
            ref = b.get("ref") or {}
            agora = c["gatilhos"].get(b["nome"])
            gat = ref.get("gatilho", "—") + (f" (agora {br(agora)} m)" if agora is not None else "")
            oc = (f"{br(b['ocup'], 1 if b['ocup'] < 10 else 0)}%") if b.get("ocup") is not None else "—"
            M.append(f"| {ref.get('rotulo', b['nome'])} | {oc} | {int(b.get('abertas') or 0)} / {int(b.get('fechadas') or 0)} | {gat} |")
        M.append("")
    if c["rios"]:
        M.append("| Régua | Nível | Δ 3 h |")
        M.append("|---|---:|---:|")
        for r in c["rios"][:8]:
            d = r.get("delta3h")
            M.append(f"| {r['nome']} | {br(r['nivel'])} m | {'—' if d is None else seta(d) + ' ' + br(d)} |")
        M.append("")
    M.append("**Cenários**")
    for e, prob, t in c["cenarios"][:4]:
        M.append(f"- {t}")
    M.append("")
    extra = []
    it = (c.get("prev") or {}).get("Itajaí")
    if it:
        raj = max((h[3] or 0) for h in it["horas"])
        extra.append(f"Itajaí 24 h: {min(h[1] for h in it['horas']):.0f}–{max(h[1] for h in it['horas']):.0f} °C, "
                     f"chuva {br(it['p24'], 0)} mm ({it['prob']}%), rajada até {raj:.0f} km/h")
    m, lu = c.get("mare"), c["lua"]
    if m and m["proximos"]:
        extra.append("Maré: " + ", ".join(f"{'preamar' if t == 'preamar' else 'baixa-mar'} {d:%H:%M}"
                                          for t, d, v in m["proximos"][:4]) + f" · {lu['nome']}")
    if extra:
        M.append("<sub>" + " · ".join(extra) + "</sub>")
    if aviso:
        M.append("")
        M.append(f"> **{titulo_aviso}** — {aviso[:280]}")
    M.append("")
    M.append(f"<sub>Fontes: Defesa Civil de Blumenau/AlertaBlu, Defesa Civil de SC, ANA, Open-Meteo/GloFAS. "
             f"Estimativa de nível é experimental; a palavra oficial é da Defesa Civil. [Painel completo]({url}/)</sub>")
    return "\n".join(M)


# ------------------------------------------------------------ principal
def main():
    modo = (os.environ.get("MODO") or "normal").lower()
    blu_serie, faixas = blumenau_oficial()
    rios, barragens, chuvas = estacoes_estado()
    hist = historico(rios, barragens)
    prev = previsao()
    mar = mare()
    moon = lua(AGORA)
    ana, tok = ana_complementar()
    titulo_aviso, aviso = aviso_blumenau()

    blu_nivel = blu_serie[-1][1] if blu_serie else None
    blu_d3 = tendencia(blu_serie, 3)
    faixa = faixa_de(blu_nivel or 0, faixas)
    obs = [c for c in chuvas if c["nome"] not in ("",)]
    obs24 = round(sum(c["h24"] for c in obs) / len(obs), 1) if obs else 0
    obs72 = round(sum(c["h72"] for c in obs) / len(obs), 1) if obs else 0
    cen, p24, p72 = cenarios(blu_nivel, blu_d3, faixa, rios, barragens, obs, prev, mar, moon)
    try:
        pn = previsao_nivel(blu_nivel, tok)
    except Exception as e:  # noqa
        log("previsão de nível falhou:", repr(e))
        pn = None
    if pn and pn["modo"] == "nivel":
        pico = max(x for x in pn["h"] if x is not None)
        fx = faixa_de(pico, faixas)
        if fx["nome"] != faixa["nome"]:
            cen.insert(0, ("📈", "estimativa", f"Nível estimado em Blumenau pode chegar a ~{br(pico, 1)} m ({fx['nome']}) "
                                               f"nos próximos dias."))
    bacia = [v for k, v in (prev or {}).items() if k != "Itajaí"]
    p48 = round(sum(v["p48"] for v in bacia) / len(bacia), 0) if bacia else None

    ctx = dict(blu_serie=blu_serie, faixas=faixas, faixa=faixa, blu_nivel=blu_nivel, rios=rios, barragens=barragens,
               ana=ana, chuva_obs24=obs24, chuva_obs72=obs72, p24=p24, p48=p48, p72=p72, prev=prev, mare=mar,
               lua=moon, cenarios=cen, prev_nivel=pn)
    niveis_agora = {r["nome"]: r["nivel"] for r in rios}
    niveis_agora["Blumenau"] = blu_nivel
    ctx["gatilhos"] = {b["nome"]: niveis_agora.get((b.get("ref") or {}).get("gatilho_estacao")) for b in barragens}

    # ---------- texto do WhatsApp
    L = []
    emo_f = {"Normalidade": "🟢", "Observação": "🟡", "Atenção": "🟠", "Alerta": "🔴", "Alerta Máximo": "🟣"}.get(faixa["nome"], "⚪")
    L.append(f"🌊 *Rio Itajaí — {AGORA:%d/%m %H:%M}*")
    if blu_nivel is not None:
        L.append(f"{emo_f} Blumenau: *{blu_nivel:.2f} m* ({faixa['nome']}) {seta(blu_d3)}"
                 + ("" if blu_d3 is None else f" {blu_d3:+.2f} m/3h"))
    if rios:
        L.append("📏 " + " · ".join(f"{r['nome']} {r['nivel']:.2f}{seta(r.get('delta3h'))}" for r in rios[:6]))
    if barragens:
        L.append("🚧 Comportas abertas/fechadas: " + " · ".join(
            f"{b['nome'].replace('Barragem ', '')} {int(b.get('abertas') or 0)}/{int(b.get('fechadas') or 0)}" for b in barragens))
    L.append(f"🌧️ Chuva bacia: {obs24:.0f} mm/24h (obs) · previsão {p24 or 0:.0f}/{p48 or 0:.0f}/{p72 or 0:.0f} mm em 24/48/72h")
    it = (prev or {}).get("Itajaí")
    if it:
        raj = max((h[3] or 0) for h in it["horas"])
        L.append(f"🌡️ Itajaí 24h: {min(h[1] for h in it['horas']):.0f}–{max(h[1] for h in it['horas']):.0f} °C · "
                 f"chuva {it['p24']:.0f} mm ({it['prob']}%) · rajada até {raj:.0f} km/h")
    if mar and mar["proximos"]:
        L.append("🌊 Maré: " + " · ".join(f"{'▲' if t == 'preamar' else '▼'} {d:%H:%M} {v:+.1f} m"
                                         for t, d, v in mar["proximos"][:4]) + f" · {moon['emoji']} {moon['nome']}")
    L.append("")
    L.append("*Cenários:*")
    for emo, prob, txt in cen[:4]:
        L.append(f"{emo} {txt}")
    if aviso:
        L.append("")
        L.append(f"📢 {titulo_aviso}: {aviso[:180]}")
    if B.get("url_pagina"):
        L.append("")
        L.append(f"📊 Painel: {B['url_pagina'].rstrip('/')}/?v={AGORA:%d%H}")
    texto = "\n".join(L)
    log("\n" + texto)

    try:
        infografico(ctx)
        pagina(ctx, texto.replace("*", ""))
    except Exception as e:  # noqa — sem gráfico, a mensagem segue
        log("infográfico falhou:", repr(e))
    # versão para e-mail (comentário no GitHub → notificação por e-mail)
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, "boletim.md"), "w", encoding="utf-8").write(email_md(ctx, blu_d3, aviso, titulo_aviso))

    if modo in ("teste", "normal", "boletim"):
        A.whatsapp(texto)


if __name__ == "__main__":
    main()
