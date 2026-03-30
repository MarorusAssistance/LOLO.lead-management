# LOLO.lead-management

Monolito FastAPI para el engine de lead management de LOLO. El core no es conversacional: expone una API HTTP limpia, usa contratos estrictos, mantiene estado durable y ejecuta un workflow lineal de busqueda, cualificacion, shortlist y drafting comercial.

## Principios

- `main` es el unico orquestador.
- `sourcer` nunca persiste ni habla con CRM.
- `qualifier` nunca persiste ni navega.
- solo `crm_write` escribe estado y CRM.
- el flujo activo es lineal y explicito.
- fail fast ante JSON invalido.
- no fabricated success.
- web-first sourcing, no LinkedIn-first.

## Arquitectura

```text
src/lolo_lead_management/
  api/              FastAPI, rutas y wiring HTTP
  application/      contenedor y casos de uso
  config/           settings por entorno
  domain/           contratos, enums y modelos Pydantic
  engine/
    agents/         system prompts por etapa
    stages/         logica de cada etapa
    main.py         orquestador lineal
  ports/            interfaces abstractas
  adapters/         Tavily, LM Studio, SQLite
  infrastructure/   SQLite y detalles de soporte
```

Pipeline actual:

`normalize -> load_state -> plan -> source -> qualify -> enrich_if_needed -> requalify -> draft -> crm_write -> continue_or_finish`

`draft` es obligatorio cuando el resultado final es `ACCEPT` o `REJECT_CLOSE_MATCH`.

## Agentes de etapa

Cada etapa tiene un `StageAgentSpec` con `role_name`, `system prompt` y contrato de salida:

- `NormalizerAgent`
- `StateLoaderAgent`
- `PlannerAgent`
- `SourcerAgent`
- `QualifierAgent`
- `EnrichmentAgent`
- `CommercialAgent`
- `CrmAgent`
- `RunControlAgent`

Los prompts estan en [src/lolo_lead_management/engine/agents/prompts](/c:/Users/maror/Projects/Personal/LOLO.lead-management/src/lolo_lead_management/engine/agents/prompts). El engine funciona sin LLM usando heuristicas deterministas, pero queda preparado para LM Studio con modelo OpenAI-compatible.

## API

- `POST /lead-search/start`
- `GET /runs/{id}`
- `POST /shortlists/{id}/select`
- `POST /query-memory/reset`
- `GET /health`

## Persistencia

Por ahora se usa SQLite local con stores y CRM sencillos:

- `LeadStore`
- `SearchRunStore`
- `ShortlistStore`
- `ExplorationMemoryStore`
- `CrmWriterPort`

La integracion futura con Notion debe entrar implementando `CrmWriterPort`, no reescribiendo el engine.

## Configuracion rapida

1. Usa Python 3.13.
2. Crea un entorno virtual.
3. Instala dependencias.
4. Crea tu `.env` a partir de `.env.example`. El engine lo carga automaticamente al arrancar.
5. Exporta `PYTHONPATH=src`.
6. Arranca la API.

Instalacion con `requirements`:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -3.13 -m pip install -r requirements-dev.txt
```

Alternativa equivalente usando `pyproject.toml`:

```powershell
py -3.13 -m pip install -e ".[dev]"
```

Arranque:

```powershell
$env:PYTHONPATH='src'
uvicorn lolo_lead_management.api.app:app --reload
```

Si cambias `.env`, reinicia el proceso para que `get_settings()` recargue la configuracion.

## Test

```powershell
$env:PYTHONPATH='src'
py -3.13 -m pytest -q -p no:cacheprovider
```

## Estado del v1

- monolito FastAPI funcionando
- workflow end-to-end funcional con shortlist real
- prompts por etapa definidos
- stores durables sobre SQLite
- Tavily y LM Studio preparados como adapters
- suite base de tests pasando

Mas detalle en [docs/architecture.md](/c:/Users/maror/Projects/Personal/LOLO.lead-management/docs/architecture.md).
