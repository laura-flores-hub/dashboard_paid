# Objective
Python code that automatically and periodically collects information from three external sources — Meta Ads, Google Ads, and HubSpot — and centralizes it in Google BigQuery. From the paid media platforms (Meta Ads and Google Ads), the investment values per campaign are extracted. From HubSpot, data on Contacts, Leads, and Deals throughout the sales funnel is extracted.

## APIs
### Meta API
#### Authentication:
The credentials are specified in the `.env` file in the following variables:
- Access Token: `META_ACCESS_TOKEN`
- Ad Account ID: `META_AD_ACCOUNT_ID`

#### Endpoints:
##### Base URL:
- https://graph.facebook.com/v25.0

##### Full URL to return campaign spend:
- {urlbase}/{META_AD_ACCOUNT_ID}/insights?fields=campaign_name%2Cspend&level=campaign&time_increment=1&time_range=%7B'since'%3A'{start_date}'%2C'until'%3A'{end_date}'%7D{pagepathbase}

##### Where:
- *start_date* = Date in yyyy-mm-dd format
- *end_date* = Date in yyyy-mm-dd format
- *pagepathbase* = &access_token={META_ACCESS_TOKEN}

#### Expected Returned Data:
- *date_start*: investment dates for each ad campaign
- *campaign_name*: names of the ad campaigns
- *spend*: investment data for the ad campaigns

#### Expected Behavior:
1. The application is expected to check, in the Meta spend table in BigQuery, whether the date column is empty.
##### 1.1 Empty column
Fetches all spend data from all campaigns. To do this, it considers the values and rules in order to respect the Meta API's security architecture and limits:
- *start_date* = 2023-09-21
- *end_date* = current date − 1
###### Mandatory Pagination (`next`):
The script implements a continuous pagination loop. It reads the first dataset and looks for a `next` key in the response, making sequential requests to the provided URL until no more pages are available.
###### Rate Limiting Protection:
Extraction is massive, which will trigger Meta's volume throttling. The script has `try/except` blocks designed to catch the following throttling errors and temporarily pause execution before retrying:
- **Code 4:** API Too Many Calls
- **Code 17:** API User Too Many Calls
- **Code 341:** Application limit reached
###### Batch Requests:
It respects the hard limit of 50 requests per batch.

2. If the date column is not empty, the script looks for the most recent date stored in the table and makes the request with the following structure:
- *start_date* = most recent date in the spend table + 1
- *end_date* = current date − 1

### Google API
#### Authentication:
The credentials are specified in the `google-ads.yaml` file in the following variables:
- Developer Token: `developer_token`
- Client ID: `client_id`
- Client Secret: `client_secret`
- Refresh Token: `refresh_token`
- Customer ID: `login_customer_id`

#### Query Method:
Data acquisition uses the official *google-ads* library for Python, via the *GoogleAdsService* service with the *search_stream* method. Queries are written in GAQL (Google Ads Query Language), which has a structure similar to SQL.

##### Query used:
SELECT<br>
 campaign.name,<br>
 segments.date,<br>
 metrics.cost_micros<br>
FROM campaign<br>
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'<br>
ORDER BY segments.date DESC

##### Where:
- *start_date*: Date in yyyy-mm-dd format
- *end_date*: Date in yyyy-mm-dd format

##### Values and their respective variables:
- *Date*: segments.date
- *Campaign Name*: campaign.name
- *Spend*: metrics.cost_micros

#### Captured Data:
- *segments.date*: investment dates for each ad campaign
- *campaign.name*: names of the ad campaigns
- *metrics.cost_micros*: investment data for the ad campaigns, in micros (1 unit = R$ 0.000001) — converted to reais by dividing by 1,000,000

#### Expected Behavior:
1. The application is expected to check, in the Google spend table in BigQuery, whether the date column is empty.
##### 1.1 If the column is empty
Fetches all spend data from all campaigns. To do this, it uses the following values:
- *start_date* = 2021-11-22
- *end_date* = current date − 1

2. If the date column is not empty, the script looks for the most recent date stored in the table and makes the request with the following structure:
- *start_date* = most recent date in the spend table + 1
- *end_date* = current date − 1

### HubSpot API
#### Authentication:
The credential is specified in the `.env` file in the following variable:
- Access Token: `TOKEN_ACESSO_HUBSPOT`

#### Query Method:
1. Lead search, with automatic pagination
2. Batch search of associated Contacts via the Batch API, in batches of up to 100 records

##### Endpoints:
Base URL:
- https://api.hubapi.com/crm/v3/objects

Data | Endpoint | Method
-- | -- | --
Leads | */leads/search* | POST
Contacts | */contacts/batch/read* | POST

<!-- later, add a 2nd version that fetches ALL data to update the history -->
##### Period Filter:
All leads created from the first day of the previous month onward are fetched, using the `hs_createdate` field with the GTE (greater than or equal) operator, in milliseconds (HubSpot standard).

##### Captured Data — Leads:
- *hs_object_id*: unique lead identifier
- *hs_createdate*: lead creation date
- *hs_pipeline_stage*: current pipeline stage
- *status_do_lead*: lead status
- *fonte_lead*: lead source
- *hs_lead_name*: lead name
- *hubspot_owner_id*: lead owner
- *hubspot_team_id*: responsible team
- *hs_lead_disqualification_reason*: disqualification reason
- *motivo_de_perda__micro_*: loss reason
- *hs_lead_source*: lead source
- *hs_lead_associated_deal_pipeline_stage*: associated deal stage
- *hs_lead_associated_deals_count*: number of associated deals
- *hs_primary_contact_id*: ID of the primary associated contact (used for batch lookup)

##### Captured Data — Contacts *(prefixed with `contact_` in the final result)*:
- *contact_firstname* / *contact_lastname*: first and last name
- *contact_email*: email
- *contact_lifecyclestage*: lifecycle stage
- *contact_numemployees*: number of employees at the company
- *contact_holding_dropdown*: whether the company is a holding or not
- *contact_cargo__fechado_*: contact's job title
- *contact_qual_o_erp_utilizado_por_sua_empresa_para_sua_gestao_financeira_*: ERP used by the company

## Google BigQuery (GBQ)
### Authentication:
The corresponding access credentials are specified in the `.env` file in the following variable:
- Service Account Data: `GOOGLE_APPLICATION_CREDENTIALS`
- Project ID: `BIGQUERY_PROJECT_ID`

### Datasets:
The IDs of each dataset are:
- *Dataset for the tables with data exported from HubSpot*: `mkt-laura-teste-api-meta.HUB_Leads_leadsgeradosecontatos`
- *Dataset for the tables with data exported from Meta*: `mkt-laura-teste-api-meta.META_Ads_gastosporcampanha`
- *Dataset for the tables with data exported from Google*: `mkt-laura-teste-api-meta.GOOGLE_Ads_gastosporcampanha`

### Tables:
The tables that must be populated and their respective columns are:
- `teste_01`

Field name | Type | Mode
-- | -- | --
dt_h_recording_data | TIMESTAMP | REQUIRED
fonte_lead | STRING | REQUIRED
hs_createdate | TIMESTAMP | REQUIRED
hs_lastmodifieddate | TIMESTAMP | NULLABLE
hs_lead_associated_deal_pipeline_stage | STRING | NULLABLE
hs_lead_associated_deals_count | INTEGER | NULLABLE
hs_lead_disqualification_reason | STRING | NULLABLE
hs_lead_name | STRING | NULLABLE
hs_lead_source | STRING | REQUIRED
hs_object_id | STRING | REQUIRED
hs_object_source_detail_1 | STRING | REQUIRED
hs_pipeline_stage | STRING | REQUIRED
hs_primary_contact_id | STRING | NULLABLE
hs_v2_date_entered_1108384633 | TIMESTAMP | NULLABLE
hs_v2_date_entered_1292804898 | TIMESTAMP | NULLABLE
hs_v2_date_entered_1296019059 | TIMESTAMP | NULLABLE
hs_v2_date_entered_attempting_stage_id_745667965 | TIMESTAMP | NULLABLE
hs_v2_date_entered_connected_stage_id_2058487257 | TIMESTAMP | NULLABLE
hs_v2_date_entered_qualified_stage_id_233247981 | TIMESTAMP | NULLABLE
hs_v2_date_entered_unqualified_stage_id_1675714327 | TIMESTAMP | NULLABLE
hs_v2_latest_time_in_connected_stage_id_2058487257 | TIMESTAMP | NULLABLE
hubspot_owner_id | STRING | NULLABLE
hubspot_team_id | STRING | NULLABLE
map_fonte_de_trafego_mais_recente_1 | STRING | NULLABLE
map_fonte_de_trafego_mais_recente_2 | STRING | NULLABLE
motivo_de_perda__micro_ | STRING | NULLABLE
produtosdirecionados | STRING | NULLABLE
status_do_lead | STRING | NULLABLE
contact_leads_associados | INTEGER | NULLABLE
contact_firstname | STRING | NULLABLE
contact_lastname | STRING | NULLABLE
contact_email | STRING | NULLABLE
contact_cargo__fechado_ | STRING | NULLABLE
contact_qual_o_erp_utilizado_por_sua_empresa_para_sua_gestao_financeira_ | STRING | NULLABLE
contact_numemployees | STRING | NULLABLE
contact_holding_dropdown | STRING | NULLABLE
contact_lifecyclestage | STRING | NULLABLE
contact_eventos__ultimos_100_ | STRING | NULLABLE

- `teste_data_meta_01`

Field name | Type | Mode
-- | -- | --
date_start | DATE | REQUIRED
campaign_name | STRING | REQUIRED
cost | FLOAT | NULLABLE
dt_h_recording_data | TIMESTAMP | REQUIRED

- `teste_data_google_01`

Field name | Type | Mode
-- | -- | --
campaign_name | STRING | REQUIRED
spend | FLOAT | NULLABLE
date | DATE | REQUIRED
dt_h_recording_data | TIMESTAMP | REQUIRED