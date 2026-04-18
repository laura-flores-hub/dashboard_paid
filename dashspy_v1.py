"""
dashspy_v1.py
Coleta dados de Meta Ads, Google Ads e HubSpot e centraliza no Google BigQuery.
"""

import os
import time
import json
import logging
from datetime import datetime, timedelta, timezone, date
from dateutil.relativedelta import relativedelta

import requests
from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account
from google.ads.googleads.client import GoogleAdsClient
from rich.logging import RichHandler

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RichHandler(rich_tracebacks=True, markup=True),
        logging.FileHandler("/home/a/apps/dashboards-alysson/temp/dashspy.log", mode="w",encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Carrega variáveis de ambiente
# ---------------------------------------------------------------------------
load_dotenv()

META_ACCESS_TOKEN  = os.environ["META_ACCESS_TOKEN"]
META_AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]
HUBSPOT_TOKEN      = os.environ["TOKEN_ACESSO_HUBSPOT"]
GBQ_PROJECT_ID     = os.environ["BIGQUERY_PROJECT_ID"]
GBQ_CREDENTIALS    = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

# ---------------------------------------------------------------------------
# Constantes de BigQuery
# ---------------------------------------------------------------------------
DATASET_META   = "META_Ads_gastosporcampanha"
TABLE_META     = "teste_data_meta_01"

DATASET_GOOGLE = "GOOGLE_Ads_gastosporcampanha"
TABLE_GOOGLE   = "teste_data_google_01"

DATASET_HUB    = "HUB_Leads_leadsgeradosecontatos"
TABLE_HUB      = "teste_01"

# Data de início histórico por fonte
META_HISTORY_START   = "2023-09-21"
GOOGLE_HISTORY_START = "2021-11-22"

# ---------------------------------------------------------------------------
# Helpers de data
# ---------------------------------------------------------------------------

def yesterday() -> str:
    """Retorna a data de ontem no formato aaaa-mm-dd."""
    return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")


def first_day_last_month_ms() -> int:
    """Retorna o primeiro dia do mês anterior em milissegundos (padrão HubSpot)."""
    today = date.today()
    first = (today.replace(day=1) - relativedelta(months=1)).replace(day=1)
    dt = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# BigQuery — cliente e utilitários
# ---------------------------------------------------------------------------

def get_bq_client() -> bigquery.Client:
    credentials = service_account.Credentials.from_service_account_file(
        GBQ_CREDENTIALS,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return bigquery.Client(project=GBQ_PROJECT_ID, credentials=credentials)


def get_last_date(client: bigquery.Client, dataset: str, table: str, date_col: str) -> str | None:
    """
    Retorna a última data registrada numa tabela BQ ou None se a tabela estiver vazia.
    """
    full_table = f"`{GBQ_PROJECT_ID}.{dataset}.{table}`"
    query = f"SELECT MAX(`{date_col}`) AS last_date FROM {full_table}"
    rows = list(client.query(query).result())
    val = rows[0].last_date if rows else None
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")
    return str(val)


def insert_rows(client: bigquery.Client, dataset: str, table: str, rows: list[dict]) -> None:
    if not rows:
        log.info("Nenhuma linha para inserir em %s.%s.", dataset, table)
        return
    full_table = f"{GBQ_PROJECT_ID}.{dataset}.{table}"
    errors = client.insert_rows_json(full_table, rows)
    if errors:
        log.error("Erros ao inserir em %s: %s", full_table, errors)
        raise RuntimeError(f"BigQuery insert_rows falhou: {errors}")
    log.info("Inseridas %d linhas em %s.", len(rows), full_table)


# ---------------------------------------------------------------------------
# Utilitários de arquivo temporário e confirmação
# ---------------------------------------------------------------------------

def save_temp(platform: str, rows: list[dict], recording_ts: str) -> str:
    """Salva os registros em um arquivo JSON temporário e retorna o caminho."""
    ts = recording_ts.replace(":", "-").replace(" ", "_")
    path = f"/home/a/apps/dashboards-alysson/temp/{platform}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2, default=str)
    log.info("Dados salvos em: %s (%d linhas)", path, len(rows))
    return path


def aguardar_confirmacao(nome: str, path: str) -> bool:
    """Exibe o caminho do arquivo e pede confirmação manual no terminal."""
    print(f"\n  Arquivo: {path}")
    resposta = input(f"  Enviar dados do {nome} para o BigQuery? [s/N]: ").strip().lower()
    return resposta == "s"


# ---------------------------------------------------------------------------
# META ADS
# Schema BQ: date_start (DATE), campaign_name (STRING), cost (FLOAT),
#            dt_h_recording_data (TIMESTAMP)
# ---------------------------------------------------------------------------

META_BASE_URL         = "https://graph.facebook.com/v25.0"
META_RATE_LIMIT_CODES = {4, 17, 341}
META_BATCH_SIZE       = 50
META_RETRY_WAIT       = 60


META_RATE_LIMIT_CODES = {1, 4, 17, 341}
META_MAX_RETRIES      = 5

def _meta_fetch_page(url: str, params: dict | None = None) -> dict:
    """Faz uma requisição GET para a Meta API com retry automático em rate-limit."""
    retries = 0
    while True:
        resp = requests.get(url, params=params, timeout=60)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise

        error = data.get("error", {})
        if error:
            code    = error.get("code")
            subcode = error.get("error_subcode")

            if code in META_RATE_LIMIT_CODES:
                retries += 1
                if retries > META_MAX_RETRIES:
                    raise RuntimeError(
                        f"Meta API error após {META_MAX_RETRIES} tentativas: {error}"
                    )
                log.warning(
                    "Meta erro transitório (código %s, subcode %s). "
                    "Tentativa %d/%d — aguardando %ss…",
                    code, subcode, retries, META_MAX_RETRIES, META_RETRY_WAIT,
                )
                time.sleep(META_RETRY_WAIT)
                continue

            raise RuntimeError(f"Meta API error: {error}")

        return data


def fetch_meta_ads(data_inicial: str, data_final: str) -> list[dict]:

    log.info("Meta Ads: buscando de %s até %s.", data_inicial, data_final)

    all_records: list[dict] = []
    current = datetime.strptime(data_inicial, "%Y-%m-%d").date()
    end     = datetime.strptime(data_final,   "%Y-%m-%d").date()

    while current <= end:
        chunk_end = min(current + relativedelta(years=1) - timedelta(days=1), end)
        log.info("  Janela: %s → %s", current, chunk_end)

        time_range  = json.dumps({"since": str(current), "until": str(chunk_end)})
        base_params = {
            "fields":         "campaign_name,spend,date_start",
            "level":          "campaign",
            "time_increment": 1,
            "time_range":     time_range,
            "access_token":   META_ACCESS_TOKEN,
            "limit":          META_BATCH_SIZE,
        }

        url  = f"{META_BASE_URL}/{META_AD_ACCOUNT_ID}/insights"
        page = 0

        while url:
            page += 1
            data    = _meta_fetch_page(url, params=base_params if page == 1 else None)
            records = data.get("data", [])
            all_records.extend(records)
            log.info("    Página %d: %d registros (total: %d).", page, len(records), len(all_records))
            url = data.get("paging", {}).get("next")

        current = chunk_end + timedelta(days=1)

    log.info("Meta Ads: %d registros obtidos no total.", len(all_records))
    return all_records


def process_meta_records(raw: list[dict], recording_ts: str) -> list[dict]:
    """Converte os registros brutos da Meta para o schema do BigQuery."""
    rows = []
    for r in raw:
        spend_val = r.get("spend")
        rows.append({
            "date_start":          r.get("date_start"),
            "campaign_name":       r.get("campaign_name", ""),
            "cost":                float(spend_val) if spend_val is not None else None,
            "dt_h_recording_data": recording_ts,
        })
    return rows


def run_meta_collect(bq: bigquery.Client, recording_ts: str) -> tuple[list[dict], str | None]:
    """Coleta, processa e salva os dados do Meta Ads. Retorna (rows, path)."""
    log.info("=== Coletando Meta Ads ===")
    last = get_last_date(bq, DATASET_META, TABLE_META, "date_start")

    if last is None:
        data_inicial = META_HISTORY_START
        log.info("Tabela Meta vazia. Carga histórica desde %s.", data_inicial)
    else:
        data_inicial = (
            datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        log.info("Última data Meta: %s. Buscando a partir de %s.", last, data_inicial)

    data_final = yesterday()

    if data_inicial > data_final:
        log.info("Meta Ads já está atualizado. Nada a coletar.")
        return [], None

    raw  = fetch_meta_ads(data_inicial, data_final)
    rows = process_meta_records(raw, recording_ts)
    path = save_temp("meta", rows, recording_ts)
    return rows, path


def send_meta(bq: bigquery.Client, rows: list[dict]) -> None:
    insert_rows(bq, DATASET_META, TABLE_META, rows)
    log.info("=== Meta Ads: %d linhas inseridas. ===", len(rows))


# ---------------------------------------------------------------------------
# GOOGLE ADS
# Schema BQ: campaign_name (STRING), spend (FLOAT), date (DATE),
#            dt_h_recording_data (TIMESTAMP)
# ---------------------------------------------------------------------------

def fetch_google_ads(data_inicial: str, data_final: str) -> list[dict]:
    """
    Busca gastos por campanha na Google Ads API para o intervalo informado.
    Utiliza google-ads Python library com search_stream.
    """
    log.info("Google Ads: buscando de %s até %s.", data_inicial, data_final)

    client     = GoogleAdsClient.load_from_storage("google-ads.yaml")
    ga_service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
            campaign.name,
            segments.date,
            metrics.cost_micros
        FROM campaign
        WHERE segments.date BETWEEN '{data_inicial}' AND '{data_final}'
        ORDER BY segments.date DESC
    """

    customer_id = client.login_customer_id
    records: list[dict] = []
    stream = ga_service.search_stream(customer_id=customer_id, query=query)

    for batch in stream:
        for row in batch.results:
            cost_brl = row.metrics.cost_micros / 1_000_000
            seg_date = row.segments.date
            records.append({
                "campaign_name": row.campaign.name,
                "spend":         cost_brl,
                "date":          seg_date,
            })

    log.info("Google Ads: %d registros obtidos.", len(records))
    return records


def process_google_records(raw: list[dict], recording_ts: str) -> list[dict]:
    """Adiciona dt_h_recording_data aos registros do Google Ads."""
    return [{**r, "dt_h_recording_data": recording_ts} for r in raw]


def run_google_collect(bq: bigquery.Client, recording_ts: str) -> tuple[list[dict], str | None]:
    """Coleta, processa e salva os dados do Google Ads. Retorna (rows, path)."""
    log.info("=== Coletando Google Ads ===")
    last = get_last_date(bq, DATASET_GOOGLE, TABLE_GOOGLE, "date")

    if last is None:
        data_inicial = GOOGLE_HISTORY_START
        log.info("Tabela Google vazia. Carga histórica desde %s.", data_inicial)
    else:
        data_inicial = (
            datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        log.info("Última data Google: %s. Buscando a partir de %s.", last, data_inicial)

    data_final = yesterday()

    if data_inicial > data_final:
        log.info("Google Ads já está atualizado. Nada a coletar.")
        return [], None

    raw  = fetch_google_ads(data_inicial, data_final)
    rows = process_google_records(raw, recording_ts)
    path = save_temp("google", rows, recording_ts)
    return rows, path


def send_google(bq: bigquery.Client, rows: list[dict]) -> None:
    insert_rows(bq, DATASET_GOOGLE, TABLE_GOOGLE, rows)
    log.info("=== Google Ads: %d linhas inseridas. ===", len(rows))


# ---------------------------------------------------------------------------
# HUBSPOT
# Schema BQ: ver tabela teste_01 no README
# ---------------------------------------------------------------------------

HUBSPOT_BASE_URL = "https://api.hubapi.com/crm/v3/objects"

LEAD_PROPERTIES = [
    "hs_object_id",
    "hs_createdate",
    "hs_lastmodifieddate",
    "hs_pipeline_stage",
    "status_do_lead",
    "fonte_lead",
    "hs_lead_name",
    "hubspot_owner_id",
    "hubspot_team_id",
    "hs_lead_disqualification_reason",
    "motivo_de_perda__micro_",
    "hs_lead_source",
    "hs_object_source_detail_1",
    "hs_lead_associated_deal_pipeline_stage",
    "hs_lead_associated_deals_count",
    "hs_primary_contact_id",
    "hs_v2_date_entered_1108384633",
    "hs_v2_date_entered_1292804898",
    "hs_v2_date_entered_1296019059",
    "hs_v2_date_entered_attempting_stage_id_745667965",
    "hs_v2_date_entered_connected_stage_id_2058487257",
    "hs_v2_date_entered_qualified_stage_id_233247981",
    "hs_v2_date_entered_unqualified_stage_id_1675714327",
    "hs_v2_latest_time_in_connected_stage_id_2058487257",
    "map_fonte_de_trafego_mais_recente_1",
    "map_fonte_de_trafego_mais_recente_2",
    "produtosdirecionados",
]

CONTACT_PROPERTIES = [
    "firstname",
    "lastname",
    "email",
    "lifecyclestage",
    "numemployees",
    "holding_dropdown",
    "cargo__fechado_",
    "qual_o_erp_utilizado_por_sua_empresa_para_sua_gestao_financeira_",
    "num_associated_deals",
    "hs_analytics_last_touch_converting_campaign",
]


def _hub_headers() -> dict:
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }


def _ms_to_bq_timestamp(ms_str: str | None) -> str | None:
    """Converte milissegundos (string) para formato ISO 8601 aceito pelo BigQuery."""
    if not ms_str:
        return None
    try:
        ts = int(ms_str) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f UTC")
    except (ValueError, OSError):
        return None


def fetch_hubspot_leads(since_ms: int) -> list[dict]:
    """Busca todos os leads criados a partir de `since_ms` com paginação automática."""
    log.info("HubSpot: buscando leads criados a partir de %s ms.", since_ms)
    url       = f"{HUBSPOT_BASE_URL}/leads/search"
    all_leads: list[dict] = []
    after: str | None = None

    while True:
        payload: dict = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "hs_createdate",
                            "operator":     "GTE",
                            "value":        str(since_ms),
                        }
                    ]
                }
            ],
            "properties": LEAD_PROPERTIES,
            "limit":      100,
        }
        if after:
            payload["after"] = after

        resp = requests.post(url, headers=_hub_headers(), json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        all_leads.extend(results)
        log.info("  Leads acumulados: %d.", len(all_leads))

        paging = data.get("paging", {})
        after  = paging.get("next", {}).get("after") if paging else None
        if not after:
            break

    log.info("HubSpot: %d leads obtidos.", len(all_leads))
    return all_leads


def fetch_hubspot_contacts_batch(contact_ids: list[str]) -> dict[str, dict]:
    """
    Busca dados de contatos em lotes de até 100 via Batch API.
    Retorna dicionário { contact_id: props }.
    """
    url        = f"{HUBSPOT_BASE_URL}/contacts/batch/read"
    contacts: dict[str, dict] = {}
    batch_size = 100

    for i in range(0, len(contact_ids), batch_size):
        batch   = contact_ids[i: i + batch_size]
        payload = {
            "inputs":     [{"id": cid} for cid in batch],
            "properties": CONTACT_PROPERTIES,
        }
        resp = requests.post(url, headers=_hub_headers(), json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("results", []):
            cid           = str(item.get("id", ""))
            contacts[cid] = item.get("properties", {})

    log.info("HubSpot: %d contatos obtidos.", len(contacts))
    return contacts


def process_hubspot_records(
    leads: list[dict],
    contacts: dict[str, dict],
    recording_ts: str,
) -> list[dict]:
    """Mescla leads + contatos e converte para o schema da tabela BigQuery teste_01."""
    rows = []
    for lead in leads:
        props = lead.get("properties", {})

        def get_ts(field: str) -> str | None:
            return _ms_to_bq_timestamp(props.get(field))

        contact_id = props.get("hs_primary_contact_id", "")
        cp         = contacts.get(str(contact_id), {}) if contact_id else {}

        row = {
            "dt_h_recording_data": recording_ts,
            # Campos REQUIRED
            "fonte_lead":                props.get("fonte_lead") or "",
            "hs_createdate":             get_ts("hs_createdate") or recording_ts,
            "hs_lead_source":            props.get("hs_lead_source") or "",
            "hs_object_id":              props.get("hs_object_id") or str(lead.get("id", "")),
            "hs_object_source_detail_1": props.get("hs_object_source_detail_1") or "",
            "hs_pipeline_stage":         props.get("hs_pipeline_stage") or "",
            # Campos NULLABLE
            "hs_lastmodifieddate":       get_ts("hs_lastmodifieddate"),
            "hs_lead_associated_deal_pipeline_stage": props.get("hs_lead_associated_deal_pipeline_stage"),
            "hs_lead_associated_deals_count": (
                int(props["hs_lead_associated_deals_count"])
                if props.get("hs_lead_associated_deals_count") else None
            ),
            "hs_lead_disqualification_reason": props.get("hs_lead_disqualification_reason"),
            "hs_lead_name":              props.get("hs_lead_name"),
            "hs_primary_contact_id":     props.get("hs_primary_contact_id"),
            "hs_v2_date_entered_1108384633":                         get_ts("hs_v2_date_entered_1108384633"),
            "hs_v2_date_entered_1292804898":                         get_ts("hs_v2_date_entered_1292804898"),
            "hs_v2_date_entered_1296019059":                         get_ts("hs_v2_date_entered_1296019059"),
            "hs_v2_date_entered_attempting_stage_id_745667965":      get_ts("hs_v2_date_entered_attempting_stage_id_745667965"),
            "hs_v2_date_entered_connected_stage_id_2058487257":      get_ts("hs_v2_date_entered_connected_stage_id_2058487257"),
            "hs_v2_date_entered_qualified_stage_id_233247981":       get_ts("hs_v2_date_entered_qualified_stage_id_233247981"),
            "hs_v2_date_entered_unqualified_stage_id_1675714327":    get_ts("hs_v2_date_entered_unqualified_stage_id_1675714327"),
            "hs_v2_latest_time_in_connected_stage_id_2058487257":    get_ts("hs_v2_latest_time_in_connected_stage_id_2058487257"),
            "hubspot_owner_id":          props.get("hubspot_owner_id"),
            "hubspot_team_id":           props.get("hubspot_team_id"),
            "map_fonte_de_trafego_mais_recente_1": props.get("map_fonte_de_trafego_mais_recente_1"),
            "map_fonte_de_trafego_mais_recente_2": props.get("map_fonte_de_trafego_mais_recente_2"),
            "motivo_de_perda__micro_":   props.get("motivo_de_perda__micro_"),
            "produtosdirecionados":      props.get("produtosdirecionados"),
            "status_do_lead":            props.get("status_do_lead"),
            # Contato
            "contact_leads_associados":  (
                int(cp["num_associated_deals"]) if cp.get("num_associated_deals") else None
            ),
            "contact_firstname":         cp.get("firstname"),
            "contact_lastname":          cp.get("lastname"),
            "contact_email":             cp.get("email"),
            "contact_cargo__fechado_":   cp.get("cargo__fechado_"),
            "contact_qual_o_erp_utilizado_por_sua_empresa_para_sua_gestao_financeira_":
                cp.get("qual_o_erp_utilizado_por_sua_empresa_para_sua_gestao_financeira_"),
            "contact_numemployees":      cp.get("numemployees"),
            "contact_holding_dropdown":  cp.get("holding_dropdown"),
            "contact_lifecyclestage":    cp.get("lifecyclestage"),
            "contact_eventos__ultimos_100_": cp.get("hs_analytics_last_touch_converting_campaign"),
        }
        rows.append(row)

    return rows


def run_hubspot_collect(bq: bigquery.Client, recording_ts: str) -> tuple[list[dict], str | None]:
    """Coleta, processa e salva os dados do HubSpot. Retorna (rows, path)."""
    log.info("=== Coletando HubSpot ===")
    since_ms = first_day_last_month_ms()

    leads = fetch_hubspot_leads(since_ms)

    contact_ids = [
        p.get("hs_primary_contact_id")
        for lead in leads
        for p in [lead.get("properties", {})]
        if p.get("hs_primary_contact_id")
    ]
    contact_ids = list(dict.fromkeys(contact_ids))

    contacts = fetch_hubspot_contacts_batch(contact_ids) if contact_ids else {}
    rows     = process_hubspot_records(leads, contacts, recording_ts)
    path     = save_temp("hubspot", rows, recording_ts)
    return rows, path


def send_hubspot(bq: bigquery.Client, rows: list[dict]) -> None:
    insert_rows(bq, DATASET_HUB, TABLE_HUB, rows)
    log.info("=== HubSpot: %d linhas inseridas. ===", len(rows))


# ---------------------------------------------------------------------------
# PIPELINE PRINCIPAL
# ---------------------------------------------------------------------------

def main() -> None:
    recording_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S UTC")
    log.info("Iniciando dashspy_v1 — registro em: %s", recording_ts)

    bq = get_bq_client()

    pipelines = [
        ("meta",    "Meta Ads",   run_meta_collect,    send_meta),
        ("google",  "Google Ads", run_google_collect,  send_google),
        ("hubspot", "HubSpot",    run_hubspot_collect, send_hubspot),
    ]

    # --- Fase 1: coleta das 3 plataformas ---
    coletados = {}
    log.info("--- Fase 1: coletando dados de todas as plataformas ---")
    for key, nome, fn_collect, fn_send in pipelines:
        try:
            rows, path = fn_collect(bq, recording_ts)
            if rows:
                coletados[key] = (nome, rows, path, fn_send)
            else:
                log.info("%s: nenhum dado novo. Pulando.", nome)
        except Exception as exc:
            log.error("Coleta [%s] falhou: %s", nome, exc, exc_info=True)

    if not coletados:
        log.warning("Nenhuma plataforma retornou dados novos. Encerrando.")
        return

    # --- Fase 2: confirmação e envio ---
    log.info("--- Fase 2: revisão e envio para o BigQuery ---")
    falhas = []
    for key, (nome, rows, path, fn_send) in coletados.items():
        if not aguardar_confirmacao(nome, path):
            log.warning("Envio do %s cancelado pelo usuário. Arquivo mantido em: %s", nome, path)
            continue
        try:
            fn_send(bq, rows)
        except Exception as exc:
            log.error("Envio [%s] falhou: %s", nome, exc, exc_info=True)
            falhas.append(nome)

    if falhas:
        log.warning("dashspy_v1 finalizado com falhas no envio: %s", ", ".join(falhas))
    else:
        log.info("dashspy_v1 finalizado com sucesso.")


if __name__ == "__main__":
    main()