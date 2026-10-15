# Alerta de cheias — Vale do Itajaí

Robô que lê a telemetria da ANA (barragens Oeste, Sul e Norte e o rio Itajaí até Blumenau e Brusque) a cada 30 minutos e manda alerta no WhatsApp pelo CallMeBot.

## Quando ele manda mensagem
- **Mudança de faixa** (normal → atenção → alerta → emergência) numa estação que tem limites em `config.json`, e de novo quando o nível baixa.
- **Subida rápida**: mais de 20 cm/h nas últimas 3 h em qualquer estação (no máximo um aviso a cada 3 h por estação).
- **Resumo diário** às 7h com o nível de todas as estações.

## Secrets (Settings → Secrets and variables → Actions)
| Nome | Conteúdo |
|---|---|
| `ANA_ID` | CPF cadastrado na ANA, só números |
| `ANA_SENHA` | senha recebida da ANA |
| `WHATSAPP_DESTINOS` | `5547999990000:APIKEY` — vários separados por vírgula, sem espaços |

## Rodar na mão
Actions → **Alerta de cheias** → *Run workflow* → escolha:
- `teste` — manda o resumo agora;
- `descobrir` — refaz a lista de estações com telemetria de nível e manda a lista;
- `normal` — igual à rodada automática.

## Ajustes (`config.json`)
- `municipios`: onde procurar estações.
- `limites_m`: faixas de nível por estação (código ou parte do nome). **Blumenau 4 / 6 / 8 m é referência inicial — confirmar com a Defesa Civil.**
- `subida_rapida_cmh`, `resumo_diario_hora`.
- `estacoes_fixas`: se preencher, usa só essas estações (`[{"codigo": "...", "nome": "...", "municipio": "..."}]`).
