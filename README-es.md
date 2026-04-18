# Objetivo
Código en Python que recolecta, de forma automatizada y periódica, información de tres fuentes externas — Meta Ads, Google Ads y HubSpot — y la centraliza en Google BigQuery. De las plataformas de medios pagos (Meta Ads y Google Ads) se extraen los valores de inversión por campaña. De HubSpot se extraen datos de Contactos, Leads y Negocios a lo largo del embudo de ventas.

## APIs
### API Meta
#### Autenticación:
Las credenciales están especificadas en el archivo `.env` en las siguientes variables:
- Token de acceso: `META_ACCESS_TOKEN`
- ID de la Cuenta Publicitaria: `META_AD_ACCOUNT_ID`

#### Endpoints:
##### URL base:
- https://graph.facebook.com/v25.0

##### URL completa para retornar los gastos de campaña:
- {urlbase}/{META_AD_ACCOUNT_ID}/insights?fields=campaign_name%2Cspend&level=campaign&time_increment=1&time_range=%7B'since'%3A'{fecha_inicial}'%2C'until'%3A'{fecha_final}'%7D{pagepathbase}

##### Donde:
- *fecha_inicial* = Fecha en formato aaaa-mm-dd
- *fecha_final* = Fecha en formato aaaa-mm-dd
- *pagepathbase* = &access_token={META_ACCESS_TOKEN}

#### Datos Esperados en la Respuesta:
- *date_start*: fechas de inversión de cada campaña publicitaria
- *campaign_name*: nombres de las campañas publicitarias
- *spend*: datos de la inversión de las campañas publicitarias

#### Funcionamiento Esperado:
1. Se espera que la aplicación verifique, en la tabla de gastos de Meta en BigQuery, si la columna de fechas está vacía.
##### 1.1 Columna vacía
Busca todos los gastos de todas las campañas. Para ello, considera los valores y reglas para respetar la arquitectura de seguridad y los límites de la API:
- *fecha_inicial* = 2023-09-21
- *fecha_final* = fecha actual − 1
###### Paginación Obligatoria (`next`):
El script implementa un bucle de paginación continua. Lee el primer conjunto de datos y busca una clave `next` en la respuesta, haciendo solicitudes secuenciales a esa URL proporcionada hasta que no haya más páginas disponibles.
###### Protección Contra Rate Limiting:
La extracción es masiva, lo que activará los límites de volumen de Meta. El script cuenta con bloques `try/except` diseñados para capturar los siguientes errores de límite (throttling) y pausar la ejecución temporalmente antes de volver a intentar:
- **Código 4:** API Too Many Calls
- **Código 17:** API User Too Many Calls
- **Código 341:** Application limit reached
###### Batch Requests:
Respeta el límite estricto de 50 solicitudes por lote.

2. Si la columna de fechas no está vacía, el script busca la última fecha registrada en la tabla y realiza la solicitud con la siguiente estructura:
- *fecha_inicial* = última fecha registrada en la tabla de gastos + 1
- *fecha_final* = fecha actual − 1

### API Google
#### Autenticación:
Las credenciales están especificadas en el archivo `google-ads.yaml` en las siguientes variables:
- Developer Token: `developer_token`
- Client ID: `client_id`
- Client Secret: `client_secret`
- Refresh Token: `refresh_token`
- Customer ID: `login_customer_id`

#### Método de Consulta:
La adquisición de datos utiliza la biblioteca oficial *google-ads* para Python, a través del servicio *GoogleAdsService* con el método *search_stream*. Las consultas se hacen en GAQL (Google Ads Query Language), estructura similar a SQL.

##### Query utilizada:
SELECT<br>
 campaign.name,<br>
 segments.date,<br>
 metrics.cost_micros<br>
FROM campaign<br>
WHERE segments.date BETWEEN '{fecha_inicial}' AND '{fecha_final}'<br>
ORDER BY segments.date DESC

##### Donde:
- *fecha_inicial*: Fecha en formato aaaa-mm-dd
- *fecha_final*: Fecha en formato aaaa-mm-dd

##### Valores y sus respectivas variables:
- *Fecha*: segments.date
- *Nombre de la Campaña*: campaign.name
- *Gasto*: metrics.cost_micros

#### Datos Capturados:
- *segments.date*: fechas de inversión de cada campaña publicitaria
- *campaign.name*: nombres de las campañas publicitarias
- *metrics.cost_micros*: datos de inversión de las campañas publicitarias, en micros (1 unidad = R$ 0,000001) — convertido a reales dividiendo por 1.000.000

#### Funcionamiento Esperado:
1. Se espera que la aplicación verifique, en la tabla de gastos de Google en BigQuery, si la columna de fechas está vacía.
##### 1.1 Si la columna está vacía
Busca todos los gastos de todas las campañas. Para ello, considera los valores:
- *fecha_inicial* = 2021-11-22
- *fecha_final* = fecha actual − 1

2. Si la columna de fechas no está vacía, el script busca la última fecha registrada en la tabla y realiza la solicitud con la siguiente estructura:
- *fecha_inicial* = última fecha registrada en la tabla de gastos + 1
- *fecha_final* = fecha actual − 1

### API HubSpot
#### Autenticación:
La credencial está especificada en el `.env` en la siguiente variable:
- Token de acceso: `TOKEN_ACESSO_HUBSPOT`

#### Método de Consulta:
1. Búsqueda de Leads, con paginación automática
2. Búsqueda en lote de los Contactos asociados a través de la Batch API, en lotes de hasta 100 registros

##### Endpoints:
URL base:
- https://api.hubapi.com/crm/v3/objects

Datos | Endpoint | Método
-- | -- | --
Leads | */leads/search* | POST
Contactos | */contacts/batch/read* | POST

<!-- más adelante, insertar una 2ª versión que busque TODOS los datos para actualización del historial -->
##### Filtro de Período:
Se buscan todos los leads creados a partir del primer día del mes anterior, utilizando el campo `hs_createdate` con el operador GTE (mayor o igual), en milisegundos (estándar HubSpot).

##### Datos Capturados — Leads:
- *hs_object_id*: identificador único del lead
- *hs_createdate*: fecha de creación del lead
- *hs_pipeline_stage*: etapa actual en el pipeline
- *status_do_lead*: estado del lead
- *fonte_lead*: fuente de origen del lead
- *hs_lead_name*: nombre del lead
- *hubspot_owner_id*: responsable del lead
- *hubspot_team_id*: equipo responsable
- *hs_lead_disqualification_reason*: motivo de descalificación
- *motivo_de_perda__micro_*: motivo de pérdida
- *hs_lead_source*: fuente del lead
- *hs_lead_associated_deal_pipeline_stage*: etapa del negocio asociado
- *hs_lead_associated_deals_count*: cantidad de negocios asociados
- *hs_primary_contact_id*: ID del contacto principal asociado (utilizado para la búsqueda en lote)

##### Datos Capturados — Contactos *(con prefijo `contact_` en el resultado final)*:
- *contact_firstname* / *contact_lastname*: nombre y apellido
- *contact_email*: correo electrónico
- *contact_lifecyclestage*: etapa en el ciclo de vida
- *contact_numemployees*: número de empleados de la empresa
- *contact_holding_dropdown*: si la empresa es una holding o no
- *contact_cargo__fechado_*: cargo del contacto
- *contact_qual_o_erp_utilizado_por_sua_empresa_para_sua_gestao_financeira_*: ERP utilizado por la empresa

## Google BigQuery (GBQ)
### Autenticación:
Las respectivas credenciales de acceso están especificadas en el `.env` en la siguiente variable:
- Datos de la Service Account: `GOOGLE_APPLICATION_CREDENTIALS`
- ID del Proyecto: `BIGQUERY_PROJECT_ID`

### Datasets:
Los IDs de cada Dataset son:
- *Dataset para las tablas de datos exportados de HubSpot*: `mkt-laura-teste-api-meta.HUB_Leads_leadsgeradosecontatos`
- *Dataset para las tablas de datos exportados de Meta*: `mkt-laura-teste-api-meta.META_Ads_gastosporcampanha`
- *Dataset para las tablas de datos exportados de Google*: `mkt-laura-teste-api-meta.GOOGLE_Ads_gastosporcampanha`

### Tablas:
Las tablas que deberán ser completadas y sus respectivas columnas son:
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