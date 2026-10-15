#!/usr/bin/env python3
"""
Robô de alerta de cheias — Vale do Itajaí (barragens + rio Itajaí até Blumenau/Brusque)
Fonte: telemetria da ANA (HidroWebService). Envio: WhatsApp via CallMeBot.

Modos (variável MODO):
  normal    -> roda a cada 30 min pelo GitHub; só manda mensagem se houver alerta
               (e um resumo diário no horário definido em config.json)
  teste     -> manda já um resumo com o nível de todas as estações
  descobrir -> refaz a lista de estações telemétricas da bacia e manda a lista

Secrets (GitHub > Settings > Secrets and variables > Actions):
  ANA_ID             CPF cadastrado na ANA (só números)
  ANA_SENHA          senha recebida da ANA
  WHATSAPP_DESTINOS  5547999990000:APIKEY  (vários separados por vírgula)
"""
import json, os, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://www.ana.gov.br/hidrowebservice/EstacoesTelemetricas"
AQUI = os.path.dirname(os.path.abspath(__file__))
TZ = timezone(timedelta(hours=-3))  # horário de Brasília
CFG = json.load(open(os.path.join(AQUI, "config.json"), encoding="utf-8"))
ARQ_EST = os.path.join(AQUI, "cache", "estacoes.json")
ARQ_ESTADO = os.path.join(AQUI, "cache", "estado.json")


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def log(*a):
    print(datetime.now(TZ).strftime("%d/%m %H:%M"), *a, flush=True)


# ---------------------------------------------------------------- ANA
def http_json(url, headers, tentativas=3):
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa
            log("falha", url.split("?")[0].rsplit("/", 2)[-2], "->", e)
            time.sleep(5 * (i + 1))
    return None


def token_ana():
    ident, senha = os.environ.get("ANA_ID", "").strip(), os.environ.get("ANA_SENHA", "").strip()
    if not ident or not senha:
        sys.exit("Faltam os secrets ANA_ID / ANA_SENHA")
    j = http_json(f"{BASE}/OAUth/v1", {"Identificador": ident, "Senha": senha})
    items = (j or {}).get("items")
    tok = None
    if isinstance(items, dict):
        tok = items.get("tokenautenticacao") or items.get("tokenautenticação")
    if not tok:
        sys.exit(f"ANA não devolveu token: {str(j)[:300]}")
    return tok


def get(tok, rota, params):
    url = f"{BASE}/{rota}?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    j = http_json(url, {"Authorization": f"Bearer {tok}"})
    items = (j or {}).get("items") or []
    return items if isinstance(items, list) else [items]


def serie(tok, cod, faixa="HORA_24"):
    hoje = datetime.now(TZ).strftime("%Y-%m-%d")
    return get(tok, "HidroinfoanaSerieTelemetricaAdotada/v1", {
        "Código da Estação": cod,
        "Tipo Filtro Data": "DATA_LEITURA",
        "Data de Busca (yyyy-MM-dd)": hoje,
        "Range Intervalo de busca": faixa,
    })


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def data(v):
    s = str(v or "")[:19].replace("T", " ")
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def resumo_serie(itens):
    """Último nível (m), subida nas últimas 3 h (cm/h) e chuva 24 h (mm)."""
    cotas, chuva = [], 0.0
    for it in itens:
        d = data(it.get("Data_Hora_Medicao"))
        c = num(it.get("Cota_Adotada"))
        if d and c is not None and str(it.get("Cota_Adotada_Status", "0")) in ("0", "None", ""):
            cotas.append((d, c))
        p = num(it.get("Chuva_Adotada"))
        if p is not None and p > 0 and str(it.get("Chuva_Adotada_Status", "0")) in ("0", "None", ""):
            chuva += p
    if not cotas:
        return None
    cotas.sort()
    d_ult, c_ult = cotas[-1]
    ref = [c for d, c in cotas if d <= d_ult - timedelta(hours=3)]
    taxa = (c_ult - ref[-1]) / 3 if ref else None  # cm/h
    return {"hora": d_ult.strftime("%d/%m %H:%M"), "nivel_m": round(c_ult / 100, 2),
            "taxa_cmh": None if taxa is None else round(taxa, 1), "chuva_mm": round(chuva, 1)}


# ---------------------------------------------------------------- estações
def descobrir(tok):
    alvo = {norm(m) for m in CFG["municipios"]}
    inv = get(tok, "HidroInventarioEstacoes/v1", {"Unidade Federativa": "SC"})
    log("inventário SC:", len(inv), "estações")
    cand = [e for e in inv
            if norm(e.get("Municipio_Nome")) in alvo
            and str(e.get("Operando", "1")) in ("1", "True", "true")]
    log("candidatas nos municípios:", len(cand))
    achadas = []
    for e in cand:
        cod = str(e.get("codigoestacao") or e.get("Codigo_Estacao") or "").strip()
        if not cod:
            continue
        r = resumo_serie(serie(tok, cod))
        if r:  # só fica quem tem telemetria de nível funcionando
            achadas.append({"codigo": cod, "nome": e.get("Estacao_Nome"),
                            "municipio": e.get("Municipio_Nome"), "rio": e.get("Rio_Nome")})
            log("  ok", cod, e.get("Estacao_Nome"), r["nivel_m"], "m")
        time.sleep(1)
    os.makedirs(os.path.dirname(ARQ_EST), exist_ok=True)
    json.dump({"atualizado": datetime.now(TZ).isoformat(), "estacoes": achadas},
              open(ARQ_EST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return achadas


def estacoes(tok):
    fixas = CFG.get("estacoes_fixas") or []
    if fixas:
        return fixas
    try:
        j = json.load(open(ARQ_EST, encoding="utf-8"))
        idade = datetime.now(TZ) - datetime.fromisoformat(j["atualizado"])
        if j["estacoes"] and idade < timedelta(days=7):
            return j["estacoes"]
    except Exception:  # noqa
        pass
    return descobrir(tok)


def limites(est):
    nome = norm(f'{est.get("nome")} {est.get("municipio")}')
    for chave, lim in CFG["limites_m"].items():
        if chave.startswith("_"):
            continue
        if str(est.get("codigo")) == chave or norm(chave) in nome:
            return lim
    return None


NIVEIS = ["normal", "atencao", "alerta", "emergencia"]
ROTULO = {"normal": "normal", "atencao": "🟡 ATENÇÃO", "alerta": "🟠 ALERTA", "emergencia": "🔴 EMERGÊNCIA"}


def classe(nivel, lim):
    if not lim:
        return "normal"
    c = "normal"
    for k in NIVEIS[1:]:
        if lim.get(k) is not None and nivel >= lim[k]:
            c = k
    return c


# ---------------------------------------------------------------- WhatsApp
def whatsapp(texto):
    dest = [d.strip() for d in os.environ.get("WHATSAPP_DESTINOS", "").split(",") if d.strip()]
    if not dest:
        log("WHATSAPP_DESTINOS vazio — mensagem só no log:\n" + texto)
        return
    for d in dest:
        fone, _, key = d.partition(":")
        fone = "".join(ch for ch in fone if ch.isdigit())
        url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
            {"phone": "+" + fone, "text": texto, "apikey": key.strip()})
        for i in range(3):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    resp = r.read().decode("utf-8", "ignore")
                ok = "queued" in resp.lower() or "sent" in resp.lower()
                log("WhatsApp", "…" + fone[-4:], "ok" if ok else "resposta: " + resp[:120])
                break
            except Exception as e:  # noqa
                log("WhatsApp falhou", "…" + fone[-4:], e)
                time.sleep(10)
        time.sleep(3)  # CallMeBot pede intervalo entre mensagens


# ---------------------------------------------------------------- principal
def linha(est, r, cl):
    t = "" if r["taxa_cmh"] is None else f' ({r["taxa_cmh"]:+.0f} cm/h)'
    p = f' · chuva 24h {r["chuva_mm"]:.0f} mm' if r["chuva_mm"] else ""
    marca = "" if cl == "normal" else f" {ROTULO[cl]}"
    return f'• {est["nome"]} ({est["municipio"]}): {r["nivel_m"]:.2f} m{t}{p}{marca} — {r["hora"]}'


def main():
    modo = (os.environ.get("MODO") or "normal").strip().lower()
    tok = token_ana()
    if modo == "descobrir":
        lst = descobrir(tok)
        whatsapp("🔎 Robô de cheias — estações com telemetria de nível encontradas:\n" +
                 "\n".join(f'• {e["codigo"]} {e["nome"]} ({e["municipio"]})' for e in lst))
        return
    lst = estacoes(tok)
    try:
        estado = json.load(open(ARQ_ESTADO, encoding="utf-8"))
    except Exception:  # noqa
        estado = {}
    agora = datetime.now(TZ)
    linhas, alertas = [], []
    taxa_lim = CFG["subida_rapida_cmh"]
    for est in lst:
        r = resumo_serie(serie(tok, est["codigo"]))
        time.sleep(1)
        if not r:
            linhas.append(f'• {est["nome"]} ({est["municipio"]}): sem dado recente')
            continue
        cl = classe(r["nivel_m"], limites(est))
        linhas.append(linha(est, r, cl))
        ant = estado.get(est["codigo"], {})
        cl_ant = ant.get("classe", "normal")
        if NIVEIS.index(cl) > NIVEIS.index(cl_ant):
            alertas.append(f'{ROTULO[cl]} — {est["nome"]} ({est["municipio"]}) chegou a {r["nivel_m"]:.2f} m')
        elif NIVEIS.index(cl) < NIVEIS.index(cl_ant):
            alertas.append(f'⬇️ {est["nome"]} ({est["municipio"]}) baixou para {r["nivel_m"]:.2f} m ({ROTULO[cl]})')
        ult_rap = ant.get("subida_rapida_em")
        rapido = r["taxa_cmh"] is not None and r["taxa_cmh"] >= taxa_lim
        if rapido and (not ult_rap or agora - datetime.fromisoformat(ult_rap) > timedelta(hours=3)):
            alertas.append(f'⚠️ Subida rápida — {est["nome"]} ({est["municipio"]}): {r["taxa_cmh"]:+.0f} cm/h, '
                           f'nível {r["nivel_m"]:.2f} m')
            ult_rap = agora.isoformat()
        estado[est["codigo"]] = {"classe": cl, "nivel_m": r["nivel_m"], "subida_rapida_em": ult_rap}

    cab = f'🌊 Rio Itajaí — {agora.strftime("%d/%m %H:%M")}'
    if alertas:
        whatsapp(cab + "\n" + "\n".join(alertas) + "\n\nSituação geral:\n" + "\n".join(linhas))
    elif modo == "teste" or (agora.hour == CFG["resumo_diario_hora"] and agora.minute < 30
                             and estado.get("_resumo") != agora.strftime("%Y-%m-%d")):
        whatsapp(cab + (" (teste)" if modo == "teste" else " — resumo diário") + "\n" + "\n".join(linhas))
        if modo != "teste":
            estado["_resumo"] = agora.strftime("%Y-%m-%d")
    else:
        log("sem alerta.\n" + "\n".join(linhas))
    os.makedirs(os.path.dirname(ARQ_ESTADO), exist_ok=True)
    json.dump(estado, open(ARQ_ESTADO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
