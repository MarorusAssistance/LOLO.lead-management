# Arquitectura

## Resumen

`LOLO.lead-management` arranca como monolito FastAPI. La elección es deliberada: menos superficie operativa, mejor trazabilidad del workflow y más facilidad para endurecer contratos antes de separar piezas. La separación real está en módulos y puertos, no en procesos.

## Capas

- `api`: boundary HTTP y serialización.
- `application`: composición del contenedor y casos de uso.
- `domain`: modelos Pydantic, enums y reglas de contrato.
- `engine`: workflow lineal, estado transitorio y stages.
- `ports`: interfaces para LLM, search, CRM y stores.
- `adapters`: implementaciones concretas de Tavily, LM Studio y SQLite.
- `infrastructure`: detalles técnicos compartidos.

## Flujo

1. `normalize` convierte texto crudo en `NormalizedLeadSearchRequest`.
2. `load_state` carga memoria global durable.
3. `plan` decide si continuar, devolver shortlist o terminar.
4. `source` ejecuta web-first sourcing y devuelve un único dossier.
5. `qualify` decide `ACCEPT`, `REJECT`, `REJECT_CLOSE_MATCH` o `ENRICH`.
6. `enrich_if_needed` hace un segundo pase acotado sobre el mismo candidato.
7. `draft` genera bundle comercial obligatorio para `ACCEPT` y close matches.
8. `crm_write` persiste run, shortlist, memoria y CRM local.
9. `continue_or_finish` cierra el run o lanza otra iteración.

## Agentes y prompting

Cada stage tiene un prompt específico y pequeño. El objetivo es que un modelo instruct como Qwen 30B vea una sola tarea, con entrada reducida y salida JSON rígida.

- `NormalizerAgent`: extracción de constraints y canonización.
- `PlannerAgent`: decisión finita y sin routing libre.
- `SourcerAgent`: playbook web-first, una pasada, un dossier.
- `QualifierAgent`: matriz de decisión y evidencia mínima.
- `EnrichmentAgent`: cierre de huecos concretos.
- `CommercialAgent`: drafting basado solo en evidencia real.

El código no delega ciegamente en el modelo:

- siempre hay schema Pydantic
- siempre hay fallback determinista
- siempre falla si el JSON es inválido

## Persistencia y CRM

SQLite es el backend inicial por simplicidad y portabilidad local. El engine ya está estructurado con puertos:

- `LeadStore`
- `SearchRunStore`
- `ShortlistStore`
- `ExplorationMemoryStore`
- `CrmWriterPort`

La implementación actual del CRM es local y sencilla. El futuro notion-engine debe reemplazar `CrmWriterPort`, no invadir `engine/`.

## Memoria de exploración

Scope actual: `global`.

Campos activos:

- `queryHistory`
- `visitedUrls`
- `searchedCompanyNames`
- `registeredLeadNames`
- `consecutiveHardMissRuns`

Semántica aplicada:

- `searchedCompanyNames`: empresas ya exploradas terminalmente.
- `registeredLeadNames`: solo leads aceptados y persistidos.
- `visitedUrls`: URLs ya inspeccionadas.
- `queryHistory`: queries usadas para deduplicar y variar sourcing.

## Evolución prevista

- cambiar SQLite por PostgreSQL sin tocar `engine/`
- enchufar notion-engine implementando `CrmWriterPort`
- mover el orquestador a LangGraph cuando el entorno tenga la dependencia disponible
- añadir adapters search/LLM adicionales manteniendo contratos
