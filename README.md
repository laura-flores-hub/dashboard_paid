# Objetivo
Código em Python que coleta, de forma automatizada e periódica, informações de três fontes externas - Meta Ads, Google Ads e Hubspot - e as centraliza no Google BigQuery. Da plataforma de mídia paga (Meta Ads e Google Ads), são extraídos os valores de investimento por campanha. Do Hubspot, são extraídos dados de Contatos, Leads e Negócios ao longo do funil de vendas.

## APIs 
### API Meta
#### Autenticação:
As credenciais estão discriminadas no arquivo .env nas seguintes variáveis:
- Token de acesso: `META_ACCESS_TOKEN`
- ID da Conta de Anúncios: `META_AD_ACCOUNT_ID`

#### Endpoints: 
##### URL base:
- https://graph.facebook.com/v25.0

##### URL Completa para retornar gastos em campanha:
- {urlbase}/{META_AD_ACCOUNT_ID}/insights?fields=campaign_name%2Cspend&level=campaign&time_increment=1&time_range=%7B'since'%3A'{data_inicial}'%2C'until'%3A'{data_final}'%7D{pagepathbase}

##### Onde:
- *data_inicial* = Data no formato aaaa-mm-dd
- *data_final* = Data no formato aaaa-mm-dd
- *pagepathbase* = &access_token={META_ACCESS_TOKEN}

#### Dados Retornados Esperados:
- *date_start*: datas de investimento de cada campanha de anúncios
- *campaign_name*: nomes das campanhas de anúncios
- *spend*: dados do investimento das campanhas de anúncios

#### Funcionamento esperado:
1. É esperado que a aplicação confirme, na tabela de gastos do Meta no Big Query, se a coluna de datas está vazia.
##### 1.1 Coluna vazia
Busca todos os gastos de todas as campanhas. Para isso, considera os valores e regras para respeitar as arquiteturas de segurança e limites da API:
- *data_inicial* = 2023-09-21
- *data_final* = data atual - 1
###### Paginação Obrigatória (`next`):
O script implementa um loop de paginação contínua. Ele lê o primeiro conjunto de dados e busca por uma chave `next` na resposta, fazendo requisições sequenciais para essa URL fornecida até que não existam mais páginas disponíveis.
###### Proteção Contra Rate Limiting:
A extração é massiva, o que acionará as travas de volume da Meta. O script possui blocos `try/except` desenhados para capturar os seguintes erros de limite (throttling) e pausar a execução temporariamente antes de tentar novamente:
- **Código 4:** API Too Many Calls
- **Código 17:** API User Too Many Calls
- **Código 341:** Application limit reached
###### Batch Requests:
Ele respeita o limite rígido de 50 requisições por lote.

2. A coluna de datas não estando vazia, o script busca pela última data informada na tabela e faz a requisição com a seguinte estrutura:
- *data_inicial* = última data informada na tabela de gastos + 1
- *data_final* = data atual - 1

### API Google
#### Autenticação:
As credenciais estão discriminadas no arquivo google-ads.yaml nas seguintes variáveis:
- Developer Token: `developer_token`
- Client ID: `client_id`
- Client Secret: `client_secret`
- Refresh Token: `refresh_token`
- Customer ID: `login_customer_id`

#### Método de consulta:
A aquisição de dados utiliza a biblioteca oficial *google-ads* para Python, por meio do serviço *GoogleAdsService* com o método *search_stream*. As consultas são feitas em GAQL (Google Ads Query Language), estrutura similar ao SQL.

##### Query utilizada:
SELECT<br>
 campaign.name,<br>
 segments.date,<br>
 metrics.cost_micros<br> 
FROM campaign<br>
WHERE segments.date BETWEEN '{data_inicial}' AND '{data_final}'<br>
ORDER BY segments.date DESC

##### Onde:
- *data_inicial*: Data no formato aaaa-mm-dd
- *data_final*: Data no formato aaaa-mm-dd

##### Valores e suas respectivas variáveis:
- *Data*: segments.date
- *Nome da Campanha*: campaign.name
- *Gasto*: metrics.cost_micros

#### Dados Capturados:
- *segments.date*: datas de investimento de cada campanha de anúncios
- *campaign.name*: nomes das campanhas de anúncios
- *metrics.cost_micros*: dados do investimento das campanhas de anúncios, em micros (1 unidade = R$ 0,000001) - convertido para reais dividindo por 1.000.000

#### Funcionamento esperado:
1. É esperado que a aplicação confirme, na tabela de gastos do Google no Big Query, se a coluna de datas está vazia.
##### 1.1 Se Coluna vazia
Busca todos os gastos de todas as campanhas. Para isso, considera os valores:
- *data_inicial* = 2021-11-22
- *data_final* = data atual - 1

2. A coluna de datas não estando vazia, o script busca pela última data informada na tabela e faz a requisição com a seguinte estrutura:
- *data_inicial* = última data informada na tabela de gastos + 1
- *data_final* = data atual - 1

### API Hubspot
#### Autenticação:
A credencial está discriminada no .env na seguinte variável:
- Token de acesso: `TOKEN_ACESSO_HUBSPOT`

#### Método de consulta:
1. Busca de Leads, com paginação automática
2. Busca em lote dos Contatos associados via Batch API, em lotes de até 100 registros

##### Endpoints:
URL base:
- https://api.hubapi.com/crm/v3/objects

Dados | Endpoint | Método
-- | -- | --
Leads | */leads/search* | POST
Contatos | */contacts/batch/read* | POST

<!-- depois, inserir uma 2ª versão que busque TODOS os dados para atualização do histórico -->
##### Filtro de período:
São buscados todos os leads criados a partir do primeiro dia do mês anterior, utilizando o campo hs_createdate com operador GTE (maior ou igual), em milissegundos (padrão HubSpot).

##### Dados Capturados — Leads:
- *hs_object_id*: identificador único do lead
- *hs_createdate*: data de criação do lead
- *hs_pipeline_stage*: estágio atual no pipeline
- *status_do_lead*: status do lead
- *fonte_lead*: fonte de origem do lead
- *hs_lead_name*: nome do lead
- *hubspot_owner_id*: responsável pelo lead
- *hubspot_team_id*: time responsável
- *hs_lead_disqualification_reason*: motivo de desqualificação
- *motivo_de_perda__micro_*: motivo de perda
- *hs_lead_source*: fonte do lead
- *hs_lead_associated_deal_pipeline_stage*: estágio do negócio associado
- *hs_lead_associated_deals_count*: quantidade de negócios associados
- *hs_primary_contact_id*: ID do contato principal associado (usado para busca em lote)

##### Dados Capturados — Contatos *(prefixados com contact_ no resultado final)*:
- *contact_firstname* / *contact_lastname*: nome e sobrenome
- *contact_email*: e-mail
- *contact_lifecyclestage*: estágio no ciclo de vida
- *contact_numemployees*: número de funcionários da empresa
- *contact_holding_dropdown*: se a empresa é uma holding ou não 
- *contact_cargo__fechado_*: cargo do contato
- *contact_qual_o_erp_utilizado_por_sua_empresa_para_sua_gestao_financeira_*: ERP utilizado pela empresa

## Google Big Query (GBQ)
### Autenticação:
As devidas credenciais de acesso estão discriminadas no .env na seguinte variável:
- Dados da Service Account: `GOOGLE_APPLICATION_CREDENTIALS`
- ID do Projeto: `BIGQUERY_PROJECT_ID`

### Datasets:
Os IDs de cada Datasets são:
- *Dataset para as tabelas dos dados exportados do Hubspot*: `mkt-laura-teste-api-meta.HUB_Leads_leadsgeradosecontatos`
- *Dataset para as tabelas dos dados exportados do Meta*: `mkt-laura-teste-api-meta.META_Ads_gastosporcampanha`
- *Dataset para as tabelas dos dados exportados do Google*: `mkt-laura-teste-api-meta.GOOGLE_Ads_gastosporcampanha`

### Tabelas:
As tabelas que deverão ser preenchidas e suas respectivas colunas são:
- `teste_01`

Field name | Type | Mode
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

Field name | Type | Mode
-- | -- | --
date_start | DATE | REQUIRED
campaign_name | STRING | REQUIRED
cost | FLOAT | NULLABLE
dt_h_recording_data | TIMESTAMP | REQUIRED

- `teste_data_google_01`

Field name | Type | Mode
-- | -- | --
campaign_name | STRING | REQUIRED
spend | FLOAT | NULLABLE
date | DATE | REQUIRED
dt_h_recording_data | TIMESTAMP | REQUIRED