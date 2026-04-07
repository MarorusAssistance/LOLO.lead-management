from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from urllib.parse import urlparse

from lolo_lead_management.domain.enums import (
    FieldEvidenceStatus,
    MatchType,
    PlannerAction,
    QualificationOutcome,
    RunStatus,
    SourceQuality,
    SourcingStatus,
)
from lolo_lead_management.domain.models import (
    AssemblyFieldAssertion,
    AssemblyResolution,
    AssembledFieldEvidence,
    AssembledLeadDossier,
    CloseMatch,
    CommercialBundle,
    CompanyCandidate,
    EvidenceDocument,
    LeadSearchConstraints,
    NormalizedLeadSearchRequest,
    PersonCandidate,
    QualificationDecision,
    QualificationRubric,
    QualificationRubricField,
    ResearchQuery,
    ResearchQueryPlan,
    SearchBudget,
    SourcePassResult,
    StageDecision,
)

DEFAULT_BUYER_TARGETS = ["ceo", "founder", "cto", "head_of_engineering", "technical_recruiter"]
DEFAULT_SEARCH_THEMES = ["genai", "ai engineering", "automation", "agentic workflows", "software company"]

BUYER_ALIASES = {
    "ceo": "ceo",
    "chief executive officer": "ceo",
    "founder": "founder",
    "cofounder": "founder",
    "cto": "cto",
    "chief technology officer": "cto",
    "head of engineering": "head_of_engineering",
    "vp engineering": "head_of_engineering",
    "engineering manager": "head_of_engineering",
    "technical recruiter": "technical_recruiter",
    "talent lead": "technical_recruiter",
    "head of talent": "technical_recruiter",
    "recruiter": "technical_recruiter",
}

COUNTRY_ALIASES = {
    "spain": "es",
    "españa": "es",
    "espana": "es",
    "madrid": "es",
    "barcelona": "es",
    "valencia": "es",
    "europe": "eu",
    "europa": "eu",
    "portugal": "pt",
    "france": "fr",
    "germany": "de",
    "uk": "gb",
    "united kingdom": "gb",
}

COUNTRY_QUERY_TERMS = {
    "es": ["Spain", "Spanish", "Madrid", "Barcelona", "Valencia"],
    "eu": ["Europe", "European"],
    "pt": ["Portugal", "Portuguese", "Lisbon", "Porto"],
    "fr": ["France", "French", "Paris", "Lyon"],
    "de": ["Germany", "German", "Berlin", "Munich"],
    "gb": ["United Kingdom", "UK", "London", "British"],
}

THEME_ALIASES = {
    "genai": "genai",
    "generative ai": "genai",
    "llm": "genai",
    "ai engineering": "ai engineering",
    "machine learning": "ai engineering",
    "automation": "automation",
    "workflow automation": "automation",
    "workflow": "agentic workflows",
    "agentic": "agentic workflows",
    "software": "software company",
    "saas": "software company",
    "it": "software company",
    "engineering": "ai engineering",
}

THEME_QUERY_TERMS = {
    "genai": ["AI", "GenAI", "generative AI"],
    "ai engineering": ["AI software", "machine learning", "data intelligence"],
    "automation": ["automation", "workflow automation", "customer automation"],
    "agentic workflows": ["AI agents", "agentic workflows", "workflow automation"],
    "software company": ["software company", "B2B software", "SaaS"],
}

DIRECTORY_DOMAINS = {
    "apollo.io",
    "crunchbase.com",
    "designrush.com",
    "eu-startups.com",
    "f6s.com",
    "linkedin.com",
    "rocketreach.co",
    "seedtable.com",
    "startupshub.catalonia.com",
    "startupstash.com",
    "techbarcelona.com",
    "techbehemoths.com",
    "tracxn.com",
    "wellfound.com",
}

PUBLISHER_DOMAINS = {
    "techcrunch.com",
    "sifted.eu",
    "eu-startups.com",
    "seedtable.com",
    "startupstash.com",
    "venturebeat.com",
    "forbes.com",
    "tech.eu",
    "thenextweb.com",
}

DISCOVERY_INCLUDE_DOMAINS = ["seedtable.com", "f6s.com", "eu-startups.com"]
BARCELONA_DISCOVERY_INCLUDE_DOMAINS = ["techbarcelona.com", "eu-startups.com", "f6s.com"]
MADRID_DISCOVERY_INCLUDE_DOMAINS = ["f6s.com", "seedtable.com", "eu-startups.com"]
DISCOVERY_EXCLUDED_DOMAINS = [
    "github.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "techcrunch.com",
    "sifted.eu",
    "revistaidees.cat",
    "app.zefyron.com",
]
ANCHOR_EXCLUDED_DOMAINS = [
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
]
BLOCKED_DISCOVERY_QUERY_TOKENS = [
    "github",
    "repository",
    "repo",
    "pull request",
    "linkedin",
    "youtube",
    "twitter",
    "facebook",
    "instagram",
]
GENERIC_COMPANY_NAME_TOKENS = {
    "ai",
    "annual",
    "annually",
    "artificial",
    "automation",
    "barcelona",
    "best",
    "business",
    "companies",
    "company",
    "com",
    "catalonia",
    "data",
    "directory",
    "ecosystem",
    "energy",
    "eu",
    "finance",
    "focused",
    "for",
    "founded",
    "has",
    "hub",
    "intelligence",
    "investment",
    "launched",
    "logo",
    "madrid",
    "manufacturing",
    "of",
    "on",
    "about",
    "page",
    "pages",
    "rankings",
    "ranking",
    "report",
    "software",
    "spain",
    "startups",
    "the",
    "top",
    "trade",
    "us",
    "watch",
}

ROLE_PATTERN = re.compile(
    r"\b(ceo|founder|cofounder|cto|chief technology officer|head of engineering|vp engineering|engineering manager|technical recruiter|talent lead|head of talent|recruiter)\b",
    re.IGNORECASE,
)
COUNT_PATTERN = re.compile(r"\b(?:find|search|busca|buscar)?\s*(\d+)\s+leads?\b", re.IGNORECASE)
SIZE_RANGE_PATTERN = re.compile(r"(?:entre|between)\s*(\d+)\s*(?:y|and|-)\s*(\d+)\s*(?:empleados|employees)", re.IGNORECASE)
SIZE_MIN_PATTERN = re.compile(r"(?:more than|over|mas de|más de)\s*(\d+)\s*(?:empleados|employees)", re.IGNORECASE)
SIZE_MAX_PATTERN = re.compile(r"(?:less than|under|menos de)\s*(\d+)\s*(?:empleados|employees)", re.IGNORECASE)
EMPLOYEE_VALUE_PATTERN = re.compile(
    r"(?:employees|empleados|team size|company size)\s*[:\-]?\s*(\d{1,5})(?:\s*(?:employees|empleados))?",
    re.IGNORECASE,
)
EMPLOYEE_RANGE_VALUE_PATTERN = re.compile(r"\b(\d{1,5})\s*[-–]\s*(\d{1,5})\s*(?:employees|empleados)\b", re.IGNORECASE)
COUNTRY_PATTERN = re.compile(r"(?:country|pais|país)\s*[:\-]?\s*([A-Za-zñáéíóú ]+)", re.IGNORECASE)
PERSON_PATTERN = re.compile(r"(?:person|persona|contact)\s*[:\-]?\s*([^\n|.]+)", re.IGNORECASE)
ROLE_VALUE_PATTERN = re.compile(r"(?:role|puesto|title|cargo)\s*[:\-]?\s*([^\n|.]+)", re.IGNORECASE)
COMPANY_PATTERN = re.compile(r"(?:company|empresa)\s*[:\-]?\s*([^\n|.]+)", re.IGNORECASE)
NAME_WITH_ROLE_PATTERN = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b.{0,60}\b(ceo|founder|cofounder|cto|head of engineering|vp engineering|engineering manager|technical recruiter|talent lead|head of talent|recruiter)\b",
    re.IGNORECASE | re.DOTALL,
)
KEY_PEOPLE_PATTERN = re.compile(r"Key people:\s*([A-Z][A-Za-zÁÉÍÓÚÑáéíóúñ.-]+(?:\s+[A-Z][A-Za-zÁÉÍÓÚÑáéíóúñ.-]+){1,3})", re.IGNORECASE)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(value)
    return output


def canonicalize_country_code(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_text(value)
    if normalized in COUNTRY_QUERY_TERMS:
        return normalized
    if normalized == "espana":
        return "es"
    for alias, code in COUNTRY_ALIASES.items():
        if alias in normalized:
            return code
    if len(normalized) == 2 and normalized.isalpha():
        return normalized
    return None


def canonicalize_buyer_targets(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        normalized = normalize_text(value).replace("-", " ")
        canonical = BUYER_ALIASES.get(normalized) or BUYER_ALIASES.get(normalized.replace("_", " "))
        if canonical and canonical not in output:
            output.append(canonical)
    return output


def canonicalize_search_themes(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        canonical = THEME_ALIASES.get(normalized)
        if canonical and canonical not in output:
            output.append(canonical)
    return output


def extract_target_count(text: str) -> int | None:
    match = COUNT_PATTERN.search(text)
    if match:
        return int(match.group(1))
    loose = re.search(r"\b(\d+)\b", text)
    if loose:
        return int(loose.group(1))
    return None


def extract_country_code(text: str) -> str | None:
    return canonicalize_country_code(text)


def extract_company_size(text: str) -> tuple[int | None, int | None]:
    if range_match := SIZE_RANGE_PATTERN.search(text):
        return int(range_match.group(1)), int(range_match.group(2))
    minimum = None
    maximum = None
    if min_match := SIZE_MIN_PATTERN.search(text):
        minimum = int(min_match.group(1))
    if max_match := SIZE_MAX_PATTERN.search(text):
        maximum = int(max_match.group(1))
    return minimum, maximum


def extract_buyer_targets(text: str) -> list[str]:
    found: list[str] = []
    for match in ROLE_PATTERN.finditer(text):
        canonical = BUYER_ALIASES.get(normalize_text(match.group(1)))
        if canonical and canonical not in found:
            found.append(canonical)
    return found


def extract_search_themes(text: str) -> list[str]:
    normalized = normalize_text(text)
    themes: list[str] = []
    for alias, canonical in THEME_ALIASES.items():
        if alias in normalized and canonical not in themes:
            themes.append(canonical)
    return themes


def build_constraints(text: str) -> LeadSearchConstraints:
    minimum, maximum = extract_company_size(text)
    preferred_country = extract_country_code(text)
    hard_constraints: list[str] = []
    relaxable_constraints: list[str] = ["named_person"]
    if preferred_country:
        hard_constraints.append("preferred_country")
    if minimum is not None or maximum is not None:
        hard_constraints.append("company_size")
    return LeadSearchConstraints(
        target_count=extract_target_count(text) or 3,
        preferred_country=preferred_country,
        preferred_regions=["eu"] if preferred_country == "es" else [],
        min_company_size=minimum,
        max_company_size=maximum,
        prefer_named_person=True,
        hard_constraints=hard_constraints,
        relaxable_constraints=relaxable_constraints,
    )


def normalize_request_payload(user_text: str, request_id: str | None, meta: dict) -> NormalizedLeadSearchRequest:
    buyers = extract_buyer_targets(user_text) or DEFAULT_BUYER_TARGETS.copy()
    themes = extract_search_themes(user_text) or DEFAULT_SEARCH_THEMES.copy()
    request = NormalizedLeadSearchRequest(
        user_text=user_text.strip(),
        constraints=build_constraints(user_text),
        buyer_targets=buyers,
        search_themes=themes,
        meta=meta,
    )
    if request_id:
        request.request_id = request_id
    return request


def repair_normalized_request(
    candidate: NormalizedLeadSearchRequest | None,
    *,
    user_text: str,
    request_id: str | None,
    meta: dict,
) -> NormalizedLeadSearchRequest:
    baseline = normalize_request_payload(user_text, request_id, meta)
    if candidate is None:
        return baseline

    repaired = candidate.model_copy(deep=True)
    repaired.user_text = user_text.strip()
    repaired.meta = meta
    repaired.request_id = request_id or repaired.request_id
    if normalize_text(repaired.request_id) in {"", "null", "none"}:
        repaired.request_id = baseline.request_id

    constraints = repaired.constraints.model_copy(deep=True)
    constraints.preferred_country = canonicalize_country_code(constraints.preferred_country) or baseline.constraints.preferred_country
    constraints.preferred_regions = dedupe_preserve_order(constraints.preferred_regions or baseline.constraints.preferred_regions)
    constraints.min_company_size = constraints.min_company_size or baseline.constraints.min_company_size
    constraints.max_company_size = constraints.max_company_size or baseline.constraints.max_company_size
    constraints.hard_constraints = dedupe_preserve_order(constraints.hard_constraints or baseline.constraints.hard_constraints)
    constraints.relaxable_constraints = dedupe_preserve_order(constraints.relaxable_constraints or baseline.constraints.relaxable_constraints)
    if constraints.preferred_country and "preferred_country" not in constraints.hard_constraints:
        constraints.hard_constraints.append("preferred_country")
    if (constraints.min_company_size is not None or constraints.max_company_size is not None) and "company_size" not in constraints.hard_constraints:
        constraints.hard_constraints.append("company_size")
    if "named_person" not in constraints.relaxable_constraints:
        constraints.relaxable_constraints.append("named_person")
    repaired.constraints = constraints
    repaired.buyer_targets = canonicalize_buyer_targets(repaired.buyer_targets) or baseline.buyer_targets
    repaired.search_themes = canonicalize_search_themes(repaired.search_themes) or baseline.search_themes
    return repaired


def relaxation_stage_from_budget(budget: SearchBudget) -> int:
    if budget.source_attempts_used >= 4:
        return 2
    if budget.source_attempts_used >= 2:
        return 1
    return 0


def decide_planner_action(*, accepted_count: int, target_count: int, budget: SearchBudget, shortlist_count: int) -> StageDecision:
    if accepted_count >= target_count:
        return StageDecision(action=PlannerAction.FINISH_ACCEPTED, relaxation_stage=relaxation_stage_from_budget(budget), reason="target_count_reached")
    if not budget.can_source():
        if shortlist_count:
            return StageDecision(action=PlannerAction.FINISH_SHORTLIST, relaxation_stage=relaxation_stage_from_budget(budget), reason="budget_exhausted_with_shortlist")
        return StageDecision(action=PlannerAction.FINISH_NO_RESULT, relaxation_stage=relaxation_stage_from_budget(budget), reason="budget_exhausted_without_results")
    return StageDecision(action=PlannerAction.SOURCE, relaxation_stage=relaxation_stage_from_budget(budget), reason="continue_sourcing")


def country_terms_for_request(request: NormalizedLeadSearchRequest) -> list[str]:
    return COUNTRY_QUERY_TERMS.get(request.constraints.preferred_country or "es", ["Spain", "Madrid", "Barcelona"])


def discovery_domains_for_request(*, city: str | None = None) -> list[str]:
    normalized_city = normalize_text(city or "")
    if normalized_city == "barcelona":
        return BARCELONA_DISCOVERY_INCLUDE_DOMAINS[:]
    if normalized_city == "madrid":
        return MADRID_DISCOVERY_INCLUDE_DOMAINS[:]
    return DISCOVERY_INCLUDE_DOMAINS[:]


def deterministic_discovery_queries(request: NormalizedLeadSearchRequest, relaxation_stage: int) -> list[ResearchQuery]:
    country_terms = country_terms_for_request(request)
    broad_country = country_terms[0]
    city_terms = [term for term in country_terms if term in {"Madrid", "Barcelona", "Valencia", "Lisbon", "Paris", "Berlin", "London"}]
    themes = request.search_themes[:]
    if relaxation_stage >= 1 and "software company" not in themes:
        themes.append("software company")
    theme_terms: list[str] = []
    for theme in themes[:2]:
        theme_terms.extend(THEME_QUERY_TERMS.get(theme, [theme]))
    theme_terms = dedupe_preserve_order(theme_terms or ["AI", "automation", "software company"])

    queries: list[ResearchQuery] = []
    discovery_domains = discovery_domains_for_request()
    for theme in theme_terms[:2]:
        queries.append(
            ResearchQuery(
                query=f"{broad_country} {theme} startup directory",
                objective="Find plausible startup company candidates from high-signal public startup directories and ecosystem hubs.",
                research_phase="company_discovery",
                source_tier_target="tier_b",
                expected_field="company_name",
                stop_if_resolved=True,
                country=request.constraints.preferred_country,
                search_depth="advanced",
                min_score=0.68,
                preferred_domains=discovery_domains,
                excluded_domains=DISCOVERY_EXCLUDED_DOMAINS,
                expected_source_types=["directory", "company_site"],
            )
        )
    for city in city_terms[:2]:
        queries.append(
            ResearchQuery(
                query=f"{city} AI startup directory",
                objective="Find city-specific AI startup company candidates from regional startup hubs and directories.",
                research_phase="company_discovery",
                source_tier_target="tier_b",
                expected_field="company_name",
                stop_if_resolved=True,
                country=request.constraints.preferred_country,
                search_depth="advanced",
                min_score=0.68,
                preferred_domains=discovery_domains_for_request(city=city),
                excluded_domains=DISCOVERY_EXCLUDED_DOMAINS,
                expected_source_types=["directory", "company_site"],
            )
        )
    if relaxation_stage >= 1:
        queries.append(
            ResearchQuery(
                query=f"{broad_country} AI startup funding",
                objective="Find AI startups with public funding or growth signals to prioritize commercially active company candidates.",
                research_phase="company_discovery",
                source_tier_target="tier_c",
                expected_field="company_name",
                country=request.constraints.preferred_country,
                search_depth="advanced",
                min_score=0.65,
                preferred_domains=discovery_domains,
                excluded_domains=DISCOVERY_EXCLUDED_DOMAINS,
                expected_source_types=["directory", "news"],
            )
        )
        queries.append(
            ResearchQuery(
                query=f"{broad_country} automation startup hiring",
                objective="Find companies with public hiring or expansion signals relevant to automation and AI services.",
                research_phase="company_discovery",
                source_tier_target="tier_b",
                expected_field="company_name",
                country=request.constraints.preferred_country,
                search_depth="advanced",
                min_score=0.62,
                preferred_domains=["eu-startups.com", "f6s.com", "techbarcelona.com"],
                excluded_domains=DISCOVERY_EXCLUDED_DOMAINS,
                expected_source_types=["directory", "job_board", "news"],
            )
        )
    return queries


def deterministic_anchor_queries(
    request: NormalizedLeadSearchRequest,
    *,
    anchor_company: str,
    missing_fields: list[str] | None = None,
) -> list[ResearchQuery]:
    missing = set(missing_fields or [])
    buyers = dedupe_preserve_order(
        [
            *[item.replace("_", " ") for item in request.buyer_targets[:3] or DEFAULT_BUYER_TARGETS[:3]],
            "founder",
            "co-founder",
            "ceo",
            "cto",
            "head of engineering",
            "vp engineering",
            "director of engineering",
            "director of data",
            "head of data",
            "ai lead",
        ]
    )
    themes = dedupe_preserve_order(request.search_themes[:2] or DEFAULT_SEARCH_THEMES[:2])
    queries: list[ResearchQuery] = [
        ResearchQuery(query=f'"{anchor_company}" official site', objective="Verify the official website and the main company entity.", research_phase="company_anchoring", candidate_company_name=anchor_company, source_tier_target="tier_a", expected_field="website", stop_if_resolved=True, exact_match=True, search_depth="advanced", min_score=0.62, excluded_domains=ANCHOR_EXCLUDED_DOMAINS, expected_source_types=["company_site"]),
        ResearchQuery(query=f'"{anchor_company}" about team contact', objective="Find company-controlled pages that confirm the website, geography, and core company identity.", research_phase="company_anchoring", candidate_company_name=anchor_company, source_tier_target="tier_a", expected_field="website", stop_if_resolved=True, exact_match=True, search_depth="advanced", min_score=0.6, excluded_domains=ANCHOR_EXCLUDED_DOMAINS, expected_source_types=["company_site"]),
        ResearchQuery(query=f'"{anchor_company}" leadership team founders', objective="Find named leaders and explicit roles on company-controlled pages.", research_phase="field_acquisition", candidate_company_name=anchor_company, source_tier_target="tier_a", expected_field="person_name", exact_match=True, search_depth="advanced", min_score=0.6, excluded_domains=ANCHOR_EXCLUDED_DOMAINS, expected_source_types=["company_site"]),
        ResearchQuery(query=f'"{anchor_company}" careers team hiring', objective="Find company-controlled or close-to-company pages with hiring and team-size clues.", research_phase="evidence_closing", candidate_company_name=anchor_company, source_tier_target="tier_a", expected_field="employee_estimate", exact_match=True, search_depth="advanced", min_score=0.58, excluded_domains=ANCHOR_EXCLUDED_DOMAINS, expected_source_types=["company_site", "job_board"]),
        ResearchQuery(query=f'"{anchor_company}" product docs blog github', objective="Find company product, docs, blog, or GitHub evidence for AI, automation, or software fit.", research_phase="field_acquisition", candidate_company_name=anchor_company, source_tier_target="tier_a", expected_field="fit_signals", exact_match=True, search_depth="advanced", min_score=0.55, excluded_domains=["linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com"], expected_source_types=["company_site", "docs", "github", "blog"]),
    ]
    if "person_name" in missing or "role_title" in missing or not missing:
        for buyer in buyers[:2]:
            queries.append(ResearchQuery(query=f'"{anchor_company}" {buyer}', objective="Find a named buyer persona and explicit role title tied to the company.", research_phase="field_acquisition", candidate_company_name=anchor_company, source_tier_target="tier_a", expected_field="role_title", exact_match=True, search_depth="advanced", min_score=0.6, excluded_domains=ANCHOR_EXCLUDED_DOMAINS, expected_source_types=["company_site", "news", "event"]))
        queries.append(ResearchQuery(query=f'"{anchor_company}" leadership founder ceo cto', objective="Use vetted company directories to corroborate named founders or technical leaders.", research_phase="field_acquisition", candidate_company_name=anchor_company, source_tier_target="tier_b", expected_field="person_name", exact_match=True, search_depth="advanced", min_score=0.58, preferred_domains=["f6s.com", "rocketreach.co", "crunchbase.com", "eu-startups.com"], excluded_domains=ANCHOR_EXCLUDED_DOMAINS, expected_source_types=["directory", "company_site"]))
    if "employee_estimate" in missing or not missing:
        queries.append(ResearchQuery(query=f'"{anchor_company}" employees team size', objective="Corroborate company size from public company profiles, directories, and hiring evidence.", research_phase="evidence_closing", candidate_company_name=anchor_company, source_tier_target="tier_b", expected_field="employee_estimate", exact_match=True, search_depth="advanced", min_score=0.56, preferred_domains=["crunchbase.com", "f6s.com", "rocketreach.co", "wellfound.com"], excluded_domains=["linkedin.com"], expected_source_types=["company_site", "directory", "job_board"]))
    if "fit_signals" in missing or not missing:
        for theme in themes:
            queries.append(ResearchQuery(query=f'"{anchor_company}" {theme}', objective="Collect corroborated evidence for AI, automation, or software fit signals.", research_phase="field_acquisition", candidate_company_name=anchor_company, source_tier_target="tier_a", expected_field="fit_signals", exact_match=True, search_depth="advanced", min_score=0.55, excluded_domains=ANCHOR_EXCLUDED_DOMAINS, expected_source_types=["company_site", "blog", "docs", "news"]))
    return queries


def build_research_query_plan(
    request: NormalizedLeadSearchRequest,
    relaxation_stage: int,
    *,
    anchor_company: str | None = None,
    missing_fields: list[str] | None = None,
    mode: str = "source",
) -> ResearchQueryPlan:
    planned_queries = deterministic_anchor_queries(request, anchor_company=anchor_company, missing_fields=missing_fields) if anchor_company else deterministic_discovery_queries(request, relaxation_stage)
    return ResearchQueryPlan(planned_queries=planned_queries, notes=[f"mode={mode}", f"relaxation_stage={relaxation_stage}"], stop_conditions=["official website resolved", "two corroborating documents for the same company", "enough evidence for hard constraints"])


def dedupe_queries(queries: list[ResearchQuery]) -> list[ResearchQuery]:
    seen: set[str] = set()
    output: list[ResearchQuery] = []
    for item in queries:
        normalized = normalize_text(item.query)
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(item)
    return output


def query_contains_blocked_discovery_terms(query: str) -> bool:
    normalized = normalize_text(query)
    return any(token in normalized for token in BLOCKED_DISCOVERY_QUERY_TOKENS)


def query_contains_premature_closing_terms(query: str) -> bool:
    normalized = normalize_text(query)
    closing_terms = [
        " founder",
        " ceo",
        " cto",
        " head of",
        " vp ",
        " director",
        " employee",
        " team size",
        " leadership",
        " hiring",
        " careers",
    ]
    return any(term in f" {normalized} " for term in closing_terms)


def query_selection_score(query: ResearchQuery) -> int:
    normalized = normalize_text(query.query)
    score = 0
    if query.research_phase == "company_discovery":
        score += 30
    if query.candidate_company_name:
        score += 25
    if query.source_tier_target == "tier_a":
        score += 14
    elif query.source_tier_target == "tier_b":
        score += 9
    elif query.source_tier_target == "tier_c":
        score -= 4
    if query.expected_field == "website":
        score += 10
    elif query.expected_field in {"person_name", "role_title"}:
        score += 6
    elif query.expected_field == "employee_estimate":
        score += 5
    if query.search_depth == "advanced":
        score += 12
    if query.preferred_domains:
        score += 10
    if query.exact_match:
        score += 8
    if "company_site" in query.expected_source_types:
        score += 6
    if "directory" in query.expected_source_types:
        score += 4
    if "job_board" in query.expected_source_types:
        score += 2
    if query_contains_blocked_discovery_terms(normalized) and query.research_phase == "company_discovery":
        score -= 100
    if query.research_phase == "company_discovery" and len(normalized.split()) > 8:
        score -= 15
    if query.source_tier_target == "tier_c" and query.expected_field in {"website", "person_name", "role_title", "employee_estimate"}:
        score -= 50
    if "under 50 employees" in normalized and query.research_phase == "company_discovery":
        score -= 8
    return score


def sanitize_research_query_plan(
    candidate: ResearchQueryPlan | None,
    *,
    fallback: ResearchQueryPlan,
    request: NormalizedLeadSearchRequest,
    anchor_company: str | None = None,
) -> ResearchQueryPlan:
    if candidate is None:
        return fallback
    sanitized: list[ResearchQuery] = []
    for item in candidate.planned_queries:
        query = " ".join(item.query.split()).strip()
        objective = " ".join(item.objective.split()).strip()
        phase = " ".join(item.research_phase.split()).strip()
        if len(query) < 3 or len(objective) < 3 or len(phase) < 3:
            continue
        if len(query.split()) > 10:
            continue
        if len(item.preferred_domains) > 4:
            continue
        if phase == "company_discovery" and query_contains_blocked_discovery_terms(query):
            continue
        if phase == "company_discovery" and query_contains_premature_closing_terms(query):
            continue
        expected_field = item.expected_field or ("company_name" if phase == "company_discovery" else "multi")
        source_tier_target = item.source_tier_target or ("tier_b" if phase == "company_discovery" else "tier_a")
        if expected_field in {"person_name", "role_title"} and not (item.candidate_company_name or anchor_company):
            continue
        if phase == "company_discovery" and expected_field in {"person_name", "role_title", "employee_estimate"}:
            continue
        if source_tier_target == "tier_c" and expected_field in {"website", "person_name", "role_title", "employee_estimate"}:
            continue
        update: dict[str, object] = {
            "query": query,
            "objective": objective,
            "research_phase": phase,
            "candidate_company_name": item.candidate_company_name or anchor_company,
            "source_tier_target": source_tier_target,
            "expected_field": expected_field,
            "stop_if_resolved": item.stop_if_resolved,
            "excluded_domains": dedupe_preserve_order([*item.excluded_domains, *(DISCOVERY_EXCLUDED_DOMAINS if phase == "company_discovery" else ANCHOR_EXCLUDED_DOMAINS)]),
        }
        if phase == "company_discovery":
            preferred_domains = item.preferred_domains or discovery_domains_for_request()
            update["preferred_domains"] = preferred_domains[:3]
            update["search_depth"] = "advanced"
            update["min_score"] = max(item.min_score, 0.65)
            update["exact_match"] = False
        elif item.candidate_company_name or anchor_company:
            update["search_depth"] = "advanced"
            update["min_score"] = max(item.min_score, 0.55)
            if expected_field == "website":
                update["stop_if_resolved"] = True
        update["country"] = item.country or request.constraints.preferred_country
        sanitized.append(item.model_copy(update=update))
    if not sanitized:
        return fallback
    return ResearchQueryPlan(planned_queries=dedupe_queries(sorted(sanitized, key=query_selection_score, reverse=True))[:6], notes=dedupe_preserve_order(candidate.notes or fallback.notes), stop_conditions=dedupe_preserve_order(candidate.stop_conditions or fallback.stop_conditions))


def choose_queries(plan: ResearchQueryPlan, query_history: list[str], *, limit: int) -> list[ResearchQuery]:
    used = {normalize_text(item) for item in query_history}
    selected: list[ResearchQuery] = []
    ranked = sorted(plan.planned_queries, key=query_selection_score, reverse=True)
    for query in ranked:
        if normalize_text(query.query) in used:
            continue
        selected.append(query)
        if len(selected) >= limit:
            break
    return selected


def merge_research_query_plans(primary: ResearchQueryPlan, fallback: ResearchQueryPlan) -> ResearchQueryPlan:
    return ResearchQueryPlan(
        planned_queries=dedupe_queries(sorted([*primary.planned_queries, *fallback.planned_queries], key=query_selection_score, reverse=True)),
        notes=dedupe_preserve_order([*primary.notes, *fallback.notes]),
        stop_conditions=dedupe_preserve_order([*primary.stop_conditions, *fallback.stop_conditions]),
    )


def domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return (urlparse(url).hostname or "").removeprefix("www.") or None
    except ValueError:
        return None


def domain_is_publisher_like(domain: str | None) -> bool:
    return bool(domain and domain in PUBLISHER_DOMAINS)


def domain_is_directory(domain: str | None) -> bool:
    return bool(domain and domain in DIRECTORY_DOMAINS)


def clean_company_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[_\-]+", " ", value)
    cleaned = re.sub(r"[^A-Za-z0-9.& ]+", " ", cleaned)
    cleaned = re.sub(r"\b(page|directory|startups?|companies?|company profile)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|")
    if not cleaned or len(cleaned) < 2:
        return None
    if len(cleaned.split()) > 8 or len(cleaned) > 80:
        return None
    return cleaned.title() if cleaned.islower() else cleaned.strip()


def is_plausible_company_name(value: str | None, *, source_domain: str | None = None) -> bool:
    cleaned = clean_company_name(value)
    if cleaned is None:
        return False
    normalized = normalize_text(cleaned)
    tokens = [token for token in re.split(r"\s+", normalized) if token]
    if not tokens:
        return False
    if normalized in {"spain", "madrid", "barcelona", "valencia", "github"}:
        return False
    if source_domain and normalized == normalize_text((source_domain or "").split(".")[0]) and (domain_is_publisher_like(source_domain) or domain_is_directory(source_domain)):
        return False
    if len(tokens) > 5:
        return False
    if len(tokens) == 1 and tokens[0] in GENERIC_COMPANY_NAME_TOKENS:
        return False
    generic_tokens = sum(1 for token in tokens if token in GENERIC_COMPANY_NAME_TOKENS)
    if generic_tokens >= max(2, len(tokens)):
        return False
    return True


def company_name_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value or ""))


def company_name_matches_anchor(candidate: str | None, anchor_company: str | None) -> bool:
    candidate_key = company_name_key(candidate)
    anchor_key = company_name_key(anchor_company)
    if not candidate_key or not anchor_key:
        return False
    return candidate_key == anchor_key or (len(candidate_key) >= 5 and candidate_key in anchor_key) or (len(anchor_key) >= 5 and anchor_key in candidate_key)


def extract_domain_company_name(url: str) -> str | None:
    domain = domain_from_url(url)
    if not domain or domain_is_publisher_like(domain) or domain_is_directory(domain):
        return None
    candidate = clean_company_name(domain.split(".")[0])
    return candidate if is_plausible_company_name(candidate, source_domain=domain) else None


def title_company_name(title: str) -> str | None:
    title = title.strip()
    if not title:
        return None
    primary = re.split(r"\s+[|\-:]\s+", title, maxsplit=1)[0].strip()
    primary = re.sub(r"^(about|team|leadership|careers|jobs|contact)\s+", "", primary, flags=re.IGNORECASE)
    primary = re.sub(r"\s+(leadership|team|engineering team|about|contact|careers|jobs|blog|docs)$", "", primary, flags=re.IGNORECASE)
    if re.search(r"\b(startups?|companies?|jobs?|rankings?|funding|raises?)\b", primary, re.IGNORECASE):
        return None
    if re.search(r"\b(engineer|developer|manager|specialist|architect|analyst|designer|recruiter|consultant|intern)\b", primary, re.IGNORECASE):
        return None
    candidate = clean_company_name(primary)
    return candidate if is_plausible_company_name(candidate) else None


def directory_title_company_name(title: str, source_domain: str | None) -> str | None:
    normalized_domain = normalize_text(source_domain or "")
    if normalized_domain == "startupshub.catalonia.com":
        primary = title.split(" - ", 1)[0].strip()
        candidate = clean_company_name(primary)
        if candidate and is_plausible_company_name(candidate, source_domain=source_domain):
            return candidate
    return None


def extract_company_candidates_from_list_text(text: str, *, source_domain: str | None = None) -> list[str]:
    candidates: list[str] = []
    patterns = [
        re.compile(r"###\s*([A-Z][A-Za-z0-9&+.'/-]{1,40}(?:\s+[A-Z][A-Za-z0-9&+.'/-]{1,40}){0,3})"),
        re.compile(r"\b([A-Z][A-Za-z0-9&+.'/-]{1,40}(?:\s+[A-Z][A-Za-z0-9&+.'/-]{1,40}){0,3})\s+(?:is|specialises|specializes|focuses|develops|provides)\b"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            candidate = clean_company_name(match.group(1))
            if candidate and is_plausible_company_name(candidate, source_domain=source_domain):
                candidates.append(candidate)
    return dedupe_preserve_order(candidates)


def extract_official_website(text: str, source_url: str) -> str | None:
    urls = re.findall(r"https?://[^\s)]+", text, flags=re.IGNORECASE)
    source_host = domain_from_url(source_url) or ""
    for candidate in urls:
        hostname = domain_from_url(candidate)
        if not hostname or hostname == source_host or domain_is_directory(hostname):
            continue
        return candidate.rstrip(".,)")
    lowered_source = source_url.lower()
    noisy_path_tokens = ["/case-studies/", "/company/", "/directory/", "/jobs/", "/job/", "/profile", "/articles/", "/blog/", "/partner", "/partners", "/perks/"]
    if source_host and not domain_is_directory(source_host) and not domain_is_publisher_like(source_host) and not any(token in lowered_source for token in noisy_path_tokens):
        return source_url
    return None


def canonicalize_website(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.hostname}"


def domain_root_name(domain: str | None) -> str | None:
    if not domain:
        return None
    root = normalize_text((domain or "").split(".")[0].replace("-", " "))
    candidate = clean_company_name(root)
    if candidate and is_plausible_company_name(candidate, source_domain=domain):
        return candidate
    return None


def extracted_official_website_from_document(document: EvidenceDocument, anchor_company: str | None = None) -> str | None:
    text = _document_text(document)
    urls = re.findall(r"https?://[^\s)]+", text, flags=re.IGNORECASE)
    source_host = domain_from_url(document.url)
    for candidate in urls:
        hostname = domain_from_url(candidate)
        if not hostname or hostname == source_host or domain_is_directory(hostname) or domain_is_publisher_like(hostname):
            continue
        website = canonicalize_website(candidate.rstrip(".,)"))
        if website:
            return website
    if source_host and not domain_is_directory(source_host) and not domain_is_publisher_like(source_host):
        if document.is_company_controlled_source:
            return canonicalize_website(document.url)
        domain_root = domain_root_name(source_host)
        if anchor_company and domain_root and company_name_matches_anchor(domain_root, anchor_company):
            return canonicalize_website(document.url)
    return None


def extract_employee_estimate_from_text(text: str) -> int | None:
    if range_match := EMPLOYEE_RANGE_VALUE_PATTERN.search(text):
        return int(range_match.group(2))
    if match := EMPLOYEE_VALUE_PATTERN.search(text):
        return int(match.group(1))
    return None


def clean_person_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"https?://\S+", " ", value)
    cleaned = re.sub(r"[/=_+(){}\[\]]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|,.;:")
    if len(cleaned) < 4 or len(cleaned) > 60:
        return None
    if any(token in cleaned.lower() for token in ["report abuse", "repository", "pull request", "administrative boundaries", "enviado"]):
        return None
    tokens = cleaned.split()
    if not 2 <= len(tokens) <= 4:
        return None
    if not all(token[:1].isalpha() for token in tokens):
        return None
    if sum(1 for token in tokens if token[:1].isupper()) < 2:
        return None
    return cleaned


def clean_role_title(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" -|,.;:")
    if len(cleaned) < 2 or len(cleaned) > 80:
        return None
    if any(token in cleaned.lower() for token in ["administrative boundaries", "enviado", "report abuse", "repository"]):
        return None
    lowered = cleaned.lower()
    if lowered.startswith(("in ", "for ", "how ", "what ", "why ", "the ")):
        return None
    if len(cleaned.split()) > 8:
        return None
    role_keywords = ("founder", "ceo", "cto", "chief", "head", "director", "manager", "lead", "vp", "president", "recruit", "talent", "engineering", "product", "operations")
    if not any(keyword in lowered for keyword in role_keywords) and len(cleaned.split()) > 4:
        return None
    return cleaned


def parse_candidate_from_text(text: str, url: str) -> tuple[PersonCandidate | None, CompanyCandidate | None]:
    person_name = PERSON_PATTERN.search(text).group(1).strip() if PERSON_PATTERN.search(text) else None
    role_title = ROLE_VALUE_PATTERN.search(text).group(1).strip() if ROLE_VALUE_PATTERN.search(text) else None
    if person_name is None and role_title is None and (match := NAME_WITH_ROLE_PATTERN.search(text)):
        person_name = match.group(1).strip()
        role_title = match.group(2).strip()
    if person_name is None and (match := KEY_PEOPLE_PATTERN.search(text)):
        person_name = match.group(1).strip()
    if role_title is None:
        for match in ROLE_PATTERN.finditer(text):
            role_title = match.group(1)
            break
    person_name = clean_person_name(person_name)
    role_title = clean_role_title(role_title)
    company_name = clean_company_name(COMPANY_PATTERN.search(text).group(1)) if COMPANY_PATTERN.search(text) else None
    country_code = extract_country_code(COUNTRY_PATTERN.search(text).group(1)) if COUNTRY_PATTERN.search(text) else extract_country_code(text)
    employee_estimate = extract_employee_estimate_from_text(text)
    if company_name is None:
        company_name = title_company_name(text.splitlines()[0] if text else "") or extract_domain_company_name(url)
    person = PersonCandidate(full_name=person_name, role_title=role_title) if person_name or role_title else None
    company = (
        CompanyCandidate(
            name=company_name,
            website=canonicalize_website(extract_official_website(text, url)),
            country_code=country_code,
            employee_estimate=employee_estimate,
        )
        if company_name
        else None
    )
    return person, company


def collect_fit_signals(text: str, request: NormalizedLeadSearchRequest) -> list[str]:
    normalized = normalize_text(text)
    signals: list[str] = []
    for theme in request.search_themes:
        if any(alias in normalized for alias, canonical in THEME_ALIASES.items() if canonical == theme):
            signals.append(theme)
    return dedupe_preserve_order(signals)


def _document_text(document: EvidenceDocument) -> str:
    return " ".join([document.title, document.snippet, document.raw_content])


def source_quality_for_document(document: EvidenceDocument, anchor_company: str | None = None) -> SourceQuality:
    domain = document.domain or domain_from_url(document.url)
    if domain_is_directory(domain):
        return SourceQuality.MEDIUM
    if domain_is_publisher_like(domain):
        return SourceQuality.LOW
    text = _document_text(document).lower()
    domain_root = normalize_text((domain or "").split(".")[0].replace("-", " "))
    if any(token in text for token in ["careers", "docs", "github", "blog", "product", "about", "team", "leadership", "contact"]):
        if not anchor_company or company_name_matches_anchor(domain_root, anchor_company):
            return SourceQuality.HIGH
    if anchor_company and anchor_company.lower() in text:
        return SourceQuality.MEDIUM
    if any(token in text for token in ["careers", "docs", "github", "blog", "product"]):
        return SourceQuality.HIGH
    return SourceQuality.MEDIUM


def source_tier_for_document(document: EvidenceDocument, anchor_company: str | None = None) -> str:
    domain = document.domain or domain_from_url(document.url)
    if domain_is_directory(domain):
        return "tier_b"
    if domain_is_publisher_like(domain):
        return "tier_c"
    text = _document_text(document).lower()
    if document.is_company_controlled_source:
        return "tier_a"
    domain_root = normalize_text((domain or "").split(".")[0].replace("-", " "))
    if any(token in text for token in ["about", "team", "leadership", "careers", "contact", "docs", "blog"]) and (
        not anchor_company or company_name_matches_anchor(domain_root, anchor_company)
    ):
        return "tier_a"
    return "tier_b"


def enrich_document_metadata(document: EvidenceDocument, *, anchor_company: str | None = None) -> EvidenceDocument:
    domain = document.domain or domain_from_url(document.url)
    quality = source_quality_for_document(document, anchor_company)
    anchor_normalized = normalize_text(anchor_company) if anchor_company else ""
    domain_root = normalize_text((domain or "").split(".")[0].replace("-", " "))
    text = normalize_text(_document_text(document))
    is_company_controlled = bool(
        anchor_company
        and domain
        and not domain_is_directory(domain)
        and not domain_is_publisher_like(domain)
        and company_name_matches_anchor(domain_root, anchor_company)
        and anchor_normalized in text
    )
    return document.model_copy(
        update={
            "domain": domain,
            "source_quality": quality,
            "source_tier": source_tier_for_document(document.model_copy(update={"domain": domain, "source_quality": quality, "is_company_controlled_source": is_company_controlled}), anchor_company),
            "is_publisher_like": domain_is_publisher_like(domain),
            "is_company_controlled_source": is_company_controlled,
        }
    )


def merge_documents(documents: list[EvidenceDocument]) -> list[EvidenceDocument]:
    by_url: dict[str, EvidenceDocument] = {}
    for document in documents:
        existing = by_url.get(document.url)
        if existing is None or len(document.raw_content) > len(existing.raw_content):
            by_url[document.url] = document
    return list(by_url.values())


def candidate_company_names_from_document(document: EvidenceDocument) -> list[str]:
    text = _document_text(document)
    candidates: list[str] = []
    source_domain = document.domain or domain_from_url(document.url)
    if document.is_publisher_like or domain_is_directory(source_domain):
        if title_candidate := directory_title_company_name(document.title, source_domain):
            candidates.append(title_candidate)
        elif title_candidate := title_company_name(document.title):
            candidates.append(title_candidate)
        candidates.extend(extract_company_candidates_from_list_text("\n".join([document.snippet, document.raw_content]), source_domain=source_domain))
        if website := extracted_official_website_from_document(document):
            if domain_candidate := domain_root_name(domain_from_url(website)):
                candidates.append(domain_candidate)
    else:
        _, company = parse_candidate_from_text(text, document.url)
        if company and company.name and is_plausible_company_name(company.name, source_domain=source_domain):
            candidates.append(company.name)
        if title_name := title_company_name(document.title):
            candidates.append(title_name)
        if not candidates and (domain_name := extract_domain_company_name(document.url)):
            candidates.append(domain_name)
    return dedupe_preserve_order([name for name in candidates if name])


def select_anchor_company(
    documents: list[EvidenceDocument],
    prior_anchor: str | None = None,
    excluded_companies: Iterable[str] | None = None,
) -> str | None:
    scores: Counter[str] = Counter()
    mentions: Counter[str] = Counter()
    official_hits: Counter[str] = Counter()
    title_hits: Counter[str] = Counter()
    excluded_names = [item for item in (excluded_companies or []) if company_name_key(item)]

    def matches_excluded(candidate: str) -> bool:
        candidate_key = company_name_key(candidate)
        for excluded in excluded_names:
            excluded_key = company_name_key(excluded)
            if not candidate_key or not excluded_key:
                continue
            if candidate_key == excluded_key:
                return True
            if candidate_key in excluded_key or excluded_key in candidate_key:
                return True
        return False

    for document in documents:
        quality_weight = {SourceQuality.HIGH: 5, SourceQuality.MEDIUM: 3, SourceQuality.LOW: 1, SourceQuality.UNKNOWN: 1}[document.source_quality]
        source_domain = document.domain or domain_from_url(document.url)
        title_candidate = directory_title_company_name(document.title, source_domain) or title_company_name(document.title)
        official_website = extracted_official_website_from_document(document)
        official_name = domain_root_name(domain_from_url(official_website))
        multi_entity = _document_is_multi_entity_listing(document)
        for candidate in candidate_company_names_from_document(document):
            if matches_excluded(candidate):
                continue
            score = quality_weight
            mentions[candidate] += 1
            if title_candidate and company_name_matches_anchor(candidate, title_candidate):
                score += 6
                title_hits[candidate] += 1
            if official_name and company_name_matches_anchor(candidate, official_name):
                score += 9
                official_hits[candidate] += 1
            if document.is_company_controlled_source:
                score += 8
            if not multi_entity:
                score += 3
            if source_domain and not domain_is_directory(source_domain) and not domain_is_publisher_like(source_domain):
                root_name = domain_root_name(source_domain)
                if root_name and company_name_matches_anchor(candidate, root_name):
                    score += 5
            if prior_anchor and candidate.lower() == prior_anchor.lower():
                score += 4
            if document.company_anchor and candidate.lower() == document.company_anchor.lower():
                score += 5
            if document.is_publisher_like:
                score -= 2
            elif domain_is_directory(source_domain) and title_candidate and not company_name_matches_anchor(candidate, title_candidate):
                score -= 3
            scores[candidate] += score
    if not scores:
        if prior_anchor and not matches_excluded(prior_anchor):
            return prior_anchor
        return None
    ranked = sorted(
        scores.items(),
        key=lambda item: (
            item[1],
            official_hits[item[0]],
            title_hits[item[0]],
            mentions[item[0]],
            len(company_name_key(item[0])),
        ),
        reverse=True,
    )
    best_name, best_score = ranked[0]
    if best_score < 6 and official_hits[best_name] == 0 and title_hits[best_name] == 0:
        return prior_anchor if prior_anchor and not matches_excluded(prior_anchor) else None
    return best_name


def _field_status_from_values(values: list[str | int], supporting: list[EvidenceDocument]) -> FieldEvidenceStatus:
    if not values:
        return FieldEvidenceStatus.UNKNOWN
    normalized = [str(value).lower() for value in values if value is not None]
    if not normalized:
        return FieldEvidenceStatus.UNKNOWN
    if len(set(normalized)) > 1:
        return FieldEvidenceStatus.CONTRADICTED
    if len(supporting) >= 2:
        return FieldEvidenceStatus.SATISFIED
    if supporting:
        return FieldEvidenceStatus.WEAKLY_SUPPORTED
    return FieldEvidenceStatus.UNKNOWN


def _source_quality_from_docs(documents: list[EvidenceDocument]) -> SourceQuality:
    if not documents:
        return SourceQuality.UNKNOWN
    if any(item.source_quality == SourceQuality.HIGH for item in documents):
        return SourceQuality.HIGH
    if any(item.source_quality == SourceQuality.MEDIUM for item in documents):
        return SourceQuality.MEDIUM
    if any(item.source_quality == SourceQuality.LOW for item in documents):
        return SourceQuality.LOW
    return SourceQuality.UNKNOWN


def _source_tier_from_docs(documents: list[EvidenceDocument]) -> str:
    if not documents:
        return "unknown"
    tiers = {item.source_tier for item in documents if item.source_tier != "unknown"}
    if not tiers:
        return "unknown"
    if len(tiers) == 1:
        return next(iter(tiers))
    return "mixed"


def _website_has_strong_support(website: str | None, documents: list[EvidenceDocument]) -> bool:
    website_domain = domain_from_url(website)
    if not website_domain or not documents:
        return False
    exact_domain_docs = [item for item in documents if domain_from_url(item.url) == website_domain]
    if any(item.is_company_controlled_source for item in exact_domain_docs):
        return True
    if any(
        not item.is_publisher_like
        and any(token in normalize_text(_document_text(item)) for token in ["about", "team", "leadership", "contact", "careers", "legal", "docs", "blog"])
        for item in exact_domain_docs
    ):
        return True
    return len(exact_domain_docs) >= 2 and not all(item.is_publisher_like for item in exact_domain_docs)


def _field_requires_corroboration(field_name: str) -> bool:
    return field_name in {"website", "employee_estimate", "person_name", "role_title"}


def _build_field_evidence(
    field_name: str,
    value: str | int | None,
    supporting: list[EvidenceDocument],
    contradicting: list[EvidenceDocument],
    *,
    note: str,
) -> AssembledFieldEvidence:
    return AssembledFieldEvidence(
        field_name=field_name,
        value=value,
        status=_field_status_from_values([value] if value is not None else [], supporting) if not contradicting else FieldEvidenceStatus.CONTRADICTED,
        supporting_evidence=supporting,
        contradicting_evidence=contradicting,
        source_quality=_source_quality_from_docs(supporting or contradicting),
        source_tier=_source_tier_from_docs(supporting or contradicting),
        support_type="corroborated" if len(supporting) >= 2 else "explicit",
        reasoning_note=note,
    )


def dedupe_documents(documents: list[EvidenceDocument]) -> list[EvidenceDocument]:
    by_url: dict[str, EvidenceDocument] = {}
    for document in documents:
        if document.url not in by_url:
            by_url[document.url] = document
    return list(by_url.values())


def build_fallback_assembled_dossier(
    *,
    request: NormalizedLeadSearchRequest,
    source_result: SourcePassResult,
    prior_dossier: AssembledLeadDossier | None = None,
) -> AssembledLeadDossier:
    documents = merge_documents([*(prior_dossier.evidence if prior_dossier else []), *source_result.documents])
    anchor_company = source_result.anchored_company_name or select_anchor_company(documents, prior_dossier.anchored_company_name if prior_dossier else None)
    if anchor_company is None:
        return AssembledLeadDossier(
            sourcing_status=SourcingStatus.NO_CANDIDATE,
            query_used=source_result.executed_queries[0].query if source_result.executed_queries else None,
            notes=dedupe_preserve_order([*source_result.notes, "unable_to_resolve_company"]),
            evidence=documents,
            research_trace=source_result.research_trace,
            documents_considered=sum(trace.documents_considered for trace in source_result.research_trace),
            documents_selected=len(documents),
        )

    anchor_lower = anchor_company.lower()
    relevant_docs: list[EvidenceDocument] = []
    company_docs: list[EvidenceDocument] = []
    country_docs: list[EvidenceDocument] = []
    size_docs: list[EvidenceDocument] = []
    person_docs: list[EvidenceDocument] = []
    role_docs: list[EvidenceDocument] = []
    website_docs: list[EvidenceDocument] = []
    contradictions: list[str] = []
    country_values: list[str] = []
    size_values: list[int] = []
    website_value: str | None = prior_dossier.company.website if prior_dossier and prior_dossier.company else None
    country_value: str | None = prior_dossier.company.country_code if prior_dossier and prior_dossier.company else None
    size_value: int | None = prior_dossier.company.employee_estimate if prior_dossier and prior_dossier.company else None
    person_name = prior_dossier.person.full_name if prior_dossier and prior_dossier.person else None
    role_title = prior_dossier.person.role_title if prior_dossier and prior_dossier.person else None

    for original in documents:
        document = enrich_document_metadata(original, anchor_company=anchor_company)
        text = _document_text(document)
        lowered = text.lower()
        if anchor_lower not in lowered and not document.is_company_controlled_source and not document.is_publisher_like:
            continue
        relevant_docs.append(document)
        person, company = parse_candidate_from_text(text, document.url)
        company_matches_anchor = False
        for candidate in candidate_company_names_from_document(document):
            if company_name_matches_anchor(candidate, anchor_company):
                company_docs.append(document)
                company_matches_anchor = True
                break
        if not company_matches_anchor and company and company.name:
            company_matches_anchor = company_name_matches_anchor(company.name, anchor_company)
        if company_matches_anchor and company and company.country_code:
            country_docs.append(document)
            country_values.append(company.country_code)
            country_value = country_values[0]
        if company_matches_anchor and company and company.employee_estimate is not None:
            size_docs.append(document)
            size_values.append(company.employee_estimate)
            size_value = size_values[0]
        if company_matches_anchor and company and company.website:
            website_docs.append(document)
            if website_value is None or document.is_company_controlled_source:
                website_value = canonicalize_website(company.website)
        elif document.is_company_controlled_source and website_value is None and document.domain:
            website_docs.append(document)
            website_value = canonicalize_website(document.url.split("/", 3)[0] + "//" + document.domain)
        if person and person.full_name and company_matches_anchor and (
            document.is_company_controlled_source
            or not domain_is_directory(document.domain)
            or document.source_quality in {SourceQuality.HIGH, SourceQuality.MEDIUM}
        ):
            person_docs.append(document)
            person_name = person.full_name
        if person and person.role_title and company_matches_anchor and (document.is_company_controlled_source or not domain_is_directory(document.domain)):
            role_docs.append(document)
            role_title = person.role_title

    if len(set(country_values)) > 1:
        contradictions.append("country has conflicting public evidence")
    if len(set(size_values)) > 1:
        contradictions.append("employee estimate has conflicting public evidence")

    evidence_pool = dedupe_documents([*company_docs, *country_docs, *size_docs, *person_docs, *role_docs, *website_docs, *relevant_docs])[:6]
    company = CompanyCandidate(name=anchor_company, website=website_value, country_code=country_value, employee_estimate=size_value)
    person = PersonCandidate(full_name=person_name, role_title=role_title) if person_name or role_title else None
    fit_signals = collect_fit_signals(" ".join(_document_text(item) for item in relevant_docs or documents), request)
    field_evidence = [
        _build_field_evidence("company_name", anchor_company, company_docs or evidence_pool[:1], [], note="Resolved company entity from multiple document mentions."),
        _build_field_evidence("website", website_value, website_docs, [], note="Website inferred from company-controlled or corroborated public pages."),
        AssembledFieldEvidence(field_name="country", value=country_value, status=FieldEvidenceStatus.CONTRADICTED if len(set(country_values)) > 1 else _field_status_from_values(country_values, country_docs), supporting_evidence=country_docs, contradicting_evidence=[], source_quality=_source_quality_from_docs(country_docs), reasoning_note="Country extracted from corroborated public evidence when available."),
        AssembledFieldEvidence(field_name="employee_estimate", value=size_value, status=FieldEvidenceStatus.CONTRADICTED if len(set(size_values)) > 1 else _field_status_from_values(size_values, size_docs), supporting_evidence=size_docs, contradicting_evidence=[], source_quality=_source_quality_from_docs(size_docs), reasoning_note="Employee estimate derived from public references to team or company size."),
        _build_field_evidence("person_name", person_name, person_docs, [], note="Named person kept only when explicitly present in the evidence."),
        _build_field_evidence("role_title", role_title, role_docs, [], note="Role title kept only when explicitly tied to the named person or company in evidence."),
        _build_field_evidence("fit_signals", ", ".join(fit_signals) if fit_signals else None, relevant_docs, [], note="Fit signals come from company product, docs, blog, hiring, or public tech references."),
    ]
    return AssembledLeadDossier(
        sourcing_status=SourcingStatus.FOUND if evidence_pool else SourcingStatus.NO_CANDIDATE,
        query_used=source_result.executed_queries[0].query if source_result.executed_queries else None,
        person=person,
        company=company,
        fit_signals=fit_signals,
        evidence=evidence_pool,
        notes=dedupe_preserve_order([*source_result.notes, f"anchored_company={anchor_company}", "assembled_by=fallback"]),
        anchored_company_name=anchor_company,
        research_trace=source_result.research_trace,
        field_evidence=field_evidence,
        contradictions=contradictions,
        evidence_quality=_source_quality_from_docs(evidence_pool),
        documents_considered=sum(trace.documents_considered for trace in source_result.research_trace),
        documents_selected=len(evidence_pool),
    )


def _documents_from_urls(urls: Iterable[str], allowed_docs: dict[str, EvidenceDocument]) -> list[EvidenceDocument]:
    selected: list[EvidenceDocument] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen or url not in allowed_docs:
            continue
        seen.add(url)
        selected.append(allowed_docs[url])
    return selected


def _resolution_assertion_map(resolution: AssemblyResolution) -> dict[str, AssemblyFieldAssertion]:
    return {item.field_name: item for item in resolution.field_assertions}


def _resolution_scalar_value(
    resolution: AssemblyResolution,
    assertions: dict[str, AssemblyFieldAssertion],
    *,
    field_name: str,
    root_value: str | int | None,
) -> str | int | None:
    if root_value is not None:
        return root_value
    assertion = assertions.get(field_name)
    if assertion is None:
        return None
    return assertion.value


def _build_resolution_field_evidence(
    *,
    field_name: str,
    value: str | int | None,
    assertion: AssemblyFieldAssertion | None,
    selected_docs: list[EvidenceDocument],
    allowed_docs: dict[str, EvidenceDocument],
    fallback_item: AssembledFieldEvidence | None,
    default_note: str,
) -> AssembledFieldEvidence:
    if assertion is not None:
        supporting = _documents_from_urls(assertion.evidence_urls, allowed_docs)
        contradicting = _documents_from_urls(assertion.contradicting_urls, allowed_docs)
        derived_tier = _source_tier_from_docs(supporting or contradicting)
        if value is None:
            return AssembledFieldEvidence(
                field_name=field_name,
                value=None,
                status=FieldEvidenceStatus.UNKNOWN,
                supporting_evidence=[],
                contradicting_evidence=contradicting,
                source_quality=_source_quality_from_docs(contradicting),
                source_tier=derived_tier,
                support_type=assertion.support_type,
                reasoning_note=default_note,
            )
        return AssembledFieldEvidence(
            field_name=field_name,
            value=value,
            status=assertion.status,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            source_quality=_source_quality_from_docs(supporting or contradicting),
            source_tier=derived_tier,
            support_type=assertion.support_type,
            reasoning_note=assertion.reasoning_note or default_note,
        )
    if value is not None and selected_docs:
        supporting = selected_docs[:3]
        return AssembledFieldEvidence(
            field_name=field_name,
            value=value,
            status=FieldEvidenceStatus.SATISFIED if len(supporting) >= 2 else FieldEvidenceStatus.WEAKLY_SUPPORTED,
            supporting_evidence=supporting,
            contradicting_evidence=[],
            source_quality=_source_quality_from_docs(supporting),
            source_tier=_source_tier_from_docs(supporting),
            support_type="corroborated" if len(supporting) >= 2 else "explicit",
            reasoning_note=default_note,
        )
    if fallback_item is not None:
        return fallback_item
    return AssembledFieldEvidence(
        field_name=field_name,
        value=value,
        status=FieldEvidenceStatus.UNKNOWN if value is None else FieldEvidenceStatus.WEAKLY_SUPPORTED,
        supporting_evidence=selected_docs[:2] if value is not None else [],
        contradicting_evidence=[],
        source_quality=_source_quality_from_docs(selected_docs[:2] if value is not None else []),
        source_tier=_source_tier_from_docs(selected_docs[:2] if value is not None else []),
        support_type="weak_inference" if value is not None else "explicit",
        reasoning_note=default_note,
    )


def sanitize_assembly_resolution(
    generated: AssemblyResolution | None,
    *,
    request: NormalizedLeadSearchRequest,
    source_result: SourcePassResult,
    prior_dossier: AssembledLeadDossier | None = None,
) -> AssembledLeadDossier:
    fallback = build_fallback_assembled_dossier(request=request, source_result=source_result, prior_dossier=prior_dossier)
    if generated is None:
        return fallback

    allowed_docs = {item.url: item for item in merge_documents([*(prior_dossier.evidence if prior_dossier else []), *source_result.documents])}
    assertions = _resolution_assertion_map(generated)
    selected_urls = dedupe_preserve_order(
        [
            *generated.selected_evidence_urls,
            *[url for item in generated.field_assertions for url in item.evidence_urls],
            *[url for item in generated.field_assertions for url in item.contradicting_urls],
        ]
    )
    evidence = _documents_from_urls(selected_urls, allowed_docs) or fallback.evidence
    fallback_field_map = {item.field_name: item for item in fallback.field_evidence}

    raw_company_name = _resolution_scalar_value(
        generated,
        assertions,
        field_name="company_name",
        root_value=generated.subject_company_name,
    )
    company_name = clean_company_name(str(raw_company_name)) if raw_company_name is not None else None
    if not is_plausible_company_name(company_name):
        company_name = fallback.company.name if fallback.company else None
    if company_name is None:
        return fallback

    website_value = _resolution_scalar_value(generated, assertions, field_name="website", root_value=generated.website)
    website = canonicalize_website(str(website_value).strip()) if isinstance(website_value, str) and website_value.strip() else None
    if website and (domain_is_publisher_like(domain_from_url(website)) or domain_is_directory(domain_from_url(website))):
        website = canonicalize_website(fallback.company.website) if fallback.company else None
    if website is None and fallback.company:
        website = canonicalize_website(fallback.company.website)

    country_value = _resolution_scalar_value(generated, assertions, field_name="country", root_value=generated.country_code)
    country_code = canonicalize_country_code(str(country_value)) if country_value is not None else None
    if country_code is None and fallback.company:
        country_code = fallback.company.country_code

    size_value = _resolution_scalar_value(
        generated,
        assertions,
        field_name="employee_estimate",
        root_value=generated.employee_estimate,
    )
    employee_estimate = int(size_value) if isinstance(size_value, int) else fallback.company.employee_estimate if fallback.company else None

    person_value = _resolution_scalar_value(generated, assertions, field_name="person_name", root_value=generated.person_name)
    person_name = clean_person_name(str(person_value).strip()) if isinstance(person_value, str) and str(person_value).strip() else None
    role_value = _resolution_scalar_value(generated, assertions, field_name="role_title", root_value=generated.role_title)
    role_title = clean_role_title(str(role_value).strip()) if isinstance(role_value, str) and str(role_value).strip() else None

    combined_text = " ".join(_document_text(item) for item in evidence).lower()
    if person_name and person_name.lower() not in combined_text:
        person_name = fallback.person.full_name if fallback.person and fallback.person.full_name and fallback.person.full_name.lower() in combined_text else None
    if person_name and not _documents_explicitly_support_person(
        person_name=person_name,
        company_name=company_name,
        company_website=website,
        documents=evidence,
    ):
        person_name = None
    if role_title and role_title.lower() not in combined_text and fallback.person and fallback.person.role_title and fallback.person.role_title.lower() in combined_text:
        role_title = fallback.person.role_title

    person = PersonCandidate(full_name=person_name, role_title=role_title) if person_name or role_title else None
    company = CompanyCandidate(name=company_name, website=website, country_code=country_code, employee_estimate=employee_estimate)

    fit_signals = [item for item in canonicalize_search_themes(generated.fit_signals) if item in request.search_themes] or fallback.fit_signals
    selected_support = evidence[:3]
    field_evidence = [
        _build_resolution_field_evidence(
            field_name="company_name",
            value=company.name,
            assertion=assertions.get("company_name"),
            selected_docs=selected_support,
            allowed_docs=allowed_docs,
            fallback_item=fallback_field_map.get("company_name"),
            default_note="Main subject company resolved from the selected public evidence.",
        ),
        _build_resolution_field_evidence(
            field_name="website",
            value=company.website,
            assertion=assertions.get("website"),
            selected_docs=selected_support,
            allowed_docs=allowed_docs,
            fallback_item=fallback_field_map.get("website"),
            default_note="Website selected from evidence that appears company-controlled or directly about the company.",
        ),
        _build_resolution_field_evidence(
            field_name="country",
            value=company.country_code,
            assertion=assertions.get("country"),
            selected_docs=selected_support,
            allowed_docs=allowed_docs,
            fallback_item=fallback_field_map.get("country"),
            default_note="Country kept only when the selected evidence ties the company to that geography.",
        ),
        _build_resolution_field_evidence(
            field_name="employee_estimate",
            value=company.employee_estimate,
            assertion=assertions.get("employee_estimate"),
            selected_docs=selected_support,
            allowed_docs=allowed_docs,
            fallback_item=fallback_field_map.get("employee_estimate"),
            default_note="Employee estimate kept only when public evidence makes company size explicit enough.",
        ),
        _build_resolution_field_evidence(
            field_name="person_name",
            value=person.full_name if person else None,
            assertion=assertions.get("person_name"),
            selected_docs=selected_support,
            allowed_docs=allowed_docs,
            fallback_item=fallback_field_map.get("person_name"),
            default_note="Named person kept only when explicitly tied to the subject company in the selected evidence.",
        ),
        _build_resolution_field_evidence(
            field_name="role_title",
            value=person.role_title if person else None,
            assertion=assertions.get("role_title"),
            selected_docs=selected_support,
            allowed_docs=allowed_docs,
            fallback_item=fallback_field_map.get("role_title"),
            default_note="Role title kept only when explicitly supported by the selected evidence.",
        ),
        _build_resolution_field_evidence(
            field_name="fit_signals",
            value=", ".join(fit_signals) if fit_signals else None,
            assertion=None,
            selected_docs=selected_support,
            allowed_docs=allowed_docs,
            fallback_item=fallback_field_map.get("fit_signals"),
            default_note="Fit signals were inferred only from selected evidence with relevant product, docs, hiring, or tech references.",
        ),
    ]
    field_evidence_map = {item.field_name: item for item in field_evidence}
    website_field = field_evidence_map["website"]
    if company.website is not None and (
        website_field.support_type == "weak_inference"
        or website_field.source_tier == "tier_c"
        or not _website_has_strong_support(company.website, website_field.supporting_evidence or evidence)
    ):
        company.website = fallback.company.website if fallback.company and _website_has_strong_support(fallback.company.website, fallback.evidence) else None
        field_evidence_map["website"] = website_field.model_copy(
            update={
                "value": company.website,
                "status": FieldEvidenceStatus.UNKNOWN if company.website is None else FieldEvidenceStatus.WEAKLY_SUPPORTED,
                "supporting_evidence": fallback.evidence[:2] if company.website else [],
                "source_tier": _source_tier_from_docs(fallback.evidence[:2]) if company.website else "unknown",
                "support_type": "corroborated" if company.website else "weak_inference",
                "reasoning_note": "Website was not sufficiently corroborated by company-controlled or matching-domain evidence, so it is not yet proven.",
            }
        )
    if company.employee_estimate is not None and not _documents_explicitly_support_employee_size(
        field_evidence_map["employee_estimate"].supporting_evidence
    ):
        company.employee_estimate = None
        field_evidence_map["employee_estimate"] = field_evidence_map["employee_estimate"].model_copy(
            update={
                "value": None,
                "status": FieldEvidenceStatus.UNKNOWN,
                "supporting_evidence": [],
                "source_tier": "unknown",
                "support_type": "weak_inference",
                "reasoning_note": "Employee size was inferred without an explicit public size statement, so it is not yet proven.",
            }
        )
    if person and person.full_name and (
        field_evidence_map["person_name"].support_type == "weak_inference"
        or field_evidence_map["person_name"].source_tier == "tier_c"
    ):
        person.full_name = None
        field_evidence_map["person_name"] = field_evidence_map["person_name"].model_copy(
            update={
                "value": None,
                "status": FieldEvidenceStatus.UNKNOWN,
                "supporting_evidence": [],
                "source_tier": "unknown",
                "support_type": "weak_inference",
                "reasoning_note": "Named person was inferred too weakly from public evidence and is not yet proven.",
            }
        )
    if person and person.role_title and not _documents_explicitly_support_role(
        role_title=person.role_title,
        person_name=person.full_name,
        documents=field_evidence_map["role_title"].supporting_evidence or evidence,
    ):
        person.role_title = None
        field_evidence_map["role_title"] = field_evidence_map["role_title"].model_copy(
            update={
                "value": None,
                "status": FieldEvidenceStatus.UNKNOWN,
                "supporting_evidence": [],
                "source_tier": "unknown",
                "support_type": "weak_inference",
                "reasoning_note": "Role title was inferred from leadership context but is not explicitly stated in the supporting evidence.",
            }
        )
    field_evidence = list(field_evidence_map.values())
    return AssembledLeadDossier(
        sourcing_status=SourcingStatus.FOUND if evidence else fallback.sourcing_status,
        query_used=source_result.executed_queries[0].query if source_result.executed_queries else fallback.query_used,
        person=person,
        company=company,
        fit_signals=fit_signals,
        evidence=evidence,
        notes=dedupe_preserve_order([*fallback.notes, *generated.confidence_notes, *generated.notes, "assembled_by=llm"]),
        anchored_company_name=company.name,
        research_trace=source_result.research_trace,
        field_evidence=field_evidence,
        contradictions=dedupe_preserve_order([*fallback.contradictions, *generated.contradictions]),
        evidence_quality=_source_quality_from_docs(evidence),
        documents_considered=sum(trace.documents_considered for trace in source_result.research_trace),
        documents_selected=len(evidence),
    )


def overlay_explicit_dossier_fields(
    assembled: AssembledLeadDossier,
    original: AssembledLeadDossier,
) -> AssembledLeadDossier:
    support = assembled.evidence[:2] or original.evidence[:2]
    field_map = {item.field_name: item for item in assembled.field_evidence}
    company = assembled.company.model_copy(deep=True) if assembled.company else original.company.model_copy(deep=True) if original.company else None
    person = assembled.person.model_copy(deep=True) if assembled.person else original.person.model_copy(deep=True) if original.person else None
    fit_signals = assembled.fit_signals or original.fit_signals

    if company and original.company:
        current_country = field_map.get("country")
        if original.company.country_code and (current_country is None or current_country.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}):
            company.country_code = original.company.country_code
            field_map["country"] = AssembledFieldEvidence(field_name="country", value=original.company.country_code, status=FieldEvidenceStatus.SATISFIED if len(support) >= 1 else FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=support, contradicting_evidence=[], source_quality=_source_quality_from_docs(support), source_tier=_source_tier_from_docs(support), support_type="corroborated" if len(support) >= 2 else "explicit", reasoning_note="Country provided explicitly in the dossier and accepted as legacy support.")
        current_size = field_map.get("employee_estimate")
        if original.company.employee_estimate is not None and (current_size is None or current_size.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}):
            company.employee_estimate = original.company.employee_estimate
            field_map["employee_estimate"] = AssembledFieldEvidence(field_name="employee_estimate", value=original.company.employee_estimate, status=FieldEvidenceStatus.SATISFIED if len(support) >= 1 else FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=support, contradicting_evidence=[], source_quality=_source_quality_from_docs(support), source_tier=_source_tier_from_docs(support), support_type="corroborated" if len(support) >= 2 else "explicit", reasoning_note="Employee estimate provided explicitly in the dossier and accepted as legacy support.")
        if original.company.website and not company.website:
            company.website = original.company.website

    if person and original.person:
        current_person = field_map.get("person_name")
        if original.person.full_name and (current_person is None or current_person.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}):
            person.full_name = original.person.full_name
            field_map["person_name"] = AssembledFieldEvidence(field_name="person_name", value=original.person.full_name, status=FieldEvidenceStatus.SATISFIED if len(support) >= 1 else FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=support, contradicting_evidence=[], source_quality=_source_quality_from_docs(support), source_tier=_source_tier_from_docs(support), support_type="corroborated" if len(support) >= 2 else "explicit", reasoning_note="Person name provided explicitly in the dossier and accepted as legacy support.")
        current_role = field_map.get("role_title")
        if original.person.role_title and (current_role is None or current_role.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}):
            person.role_title = original.person.role_title
            field_map["role_title"] = AssembledFieldEvidence(field_name="role_title", value=original.person.role_title, status=FieldEvidenceStatus.SATISFIED if len(support) >= 1 else FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=support, contradicting_evidence=[], source_quality=_source_quality_from_docs(support), source_tier=_source_tier_from_docs(support), support_type="corroborated" if len(support) >= 2 else "explicit", reasoning_note="Role title provided explicitly in the dossier and accepted as legacy support.")

    current_fit = field_map.get("fit_signals")
    if fit_signals and (current_fit is None or current_fit.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}):
        field_map["fit_signals"] = AssembledFieldEvidence(field_name="fit_signals", value=", ".join(fit_signals), status=FieldEvidenceStatus.SATISFIED if len(support) >= 1 else FieldEvidenceStatus.WEAKLY_SUPPORTED, supporting_evidence=support, contradicting_evidence=[], source_quality=_source_quality_from_docs(support), source_tier=_source_tier_from_docs(support), support_type="corroborated" if len(support) >= 2 else "explicit", reasoning_note="Fit signals provided explicitly in the dossier and accepted as legacy support.")

    return assembled.model_copy(
        update={
            "sourcing_status": original.sourcing_status,
            "company": company,
            "person": person,
            "fit_signals": fit_signals,
            "field_evidence": list(field_map.values()),
        }
    )


def qualifies_role(role_title: str | None, buyer_targets: list[str]) -> tuple[bool, bool]:
    if role_title is None:
        return False, False
    normalized = normalize_text(role_title)
    exact = any(target.replace("_", " ") in normalized for target in buyer_targets)
    adjacent = any(keyword in normalized for keyword in ["engineering", "founder", "technology", "talent", "recruit"])
    return exact, adjacent


def field_evidence_map(dossier: AssembledLeadDossier) -> dict[str, AssembledFieldEvidence]:
    return {item.field_name: item for item in dossier.field_evidence}


def _rubric_field_from_field_evidence(item: AssembledFieldEvidence | None, *, field_name: str, fallback_note: str) -> QualificationRubricField:
    if item is None:
        return QualificationRubricField(field_name=field_name, status=FieldEvidenceStatus.UNKNOWN, supporting_evidence=[], contradicting_evidence=[], source_quality=SourceQuality.UNKNOWN, source_tier="unknown", support_type="weak_inference", reasoning_note=fallback_note)
    return QualificationRubricField(field_name=field_name, status=item.status, supporting_evidence=item.supporting_evidence, contradicting_evidence=item.contradicting_evidence, source_quality=item.source_quality, source_tier=item.source_tier, support_type=item.support_type, reasoning_note=item.reasoning_note)


def _update_rubric_field(
    rubric: QualificationRubric,
    field_map: dict[str, QualificationRubricField],
    *,
    field_name: str,
    status: FieldEvidenceStatus,
    reasoning_note: str,
) -> None:
    current = field_map[field_name]
    updated = current.model_copy(update={"status": status, "reasoning_note": reasoning_note})
    field_map[field_name] = updated
    for index, item in enumerate(rubric.fields):
        if item.field_name == field_name:
            rubric.fields[index] = updated
            break


def _documents_explicitly_support_role(
    *,
    role_title: str | None,
    person_name: str | None,
    documents: list[EvidenceDocument],
) -> bool:
    if not role_title:
        return False
    normalized_role = normalize_text(role_title).replace("&", " and ")
    role_tokens = [token for token in re.split(r"[^a-z]+", normalized_role) if token]
    if not role_tokens:
        return False
    normalized_person = normalize_text(person_name) if person_name else None
    for document in documents:
        text = normalize_text(_document_text(document))
        if normalized_person and normalized_person not in text:
            continue
        if any(token in text for token in role_tokens):
            return True
    return False


def _document_is_multi_entity_listing(document: EvidenceDocument) -> bool:
    title = normalize_text(document.title)
    url = normalize_text(document.url)
    text = normalize_text(_document_text(document))
    listing_tokens = [
        "best ",
        " top ",
        "directory",
        "startups in",
        "startups to watch",
        "ranking",
        "rankings",
        "job opportunities",
        "/directory/",
        "/page/",
        " page ",
    ]
    return domain_is_publisher_like(domain_from_url(document.url)) or any(token in title or token in url or token in text for token in listing_tokens)


def document_is_multi_entity_listing(document: EvidenceDocument) -> bool:
    return _document_is_multi_entity_listing(document)


def _documents_explicitly_support_person(
    *,
    person_name: str | None,
    company_name: str | None,
    company_website: str | None,
    documents: list[EvidenceDocument],
) -> bool:
    if not person_name or not documents:
        return False
    normalized_person = normalize_text(person_name)
    normalized_company = normalize_text(company_name or "")
    website_domain = domain_from_url(company_website)
    proximity_pattern = None
    if normalized_company:
        proximity_pattern = re.compile(
            rf"{re.escape(normalized_person)}.{{0,140}}{re.escape(normalized_company)}|{re.escape(normalized_company)}.{{0,140}}{re.escape(normalized_person)}",
            re.IGNORECASE | re.DOTALL,
        )
    for document in documents:
        text = normalize_text(_document_text(document))
        if normalized_person not in text:
            continue
        if website_domain and domain_from_url(document.url) == website_domain and not document.is_publisher_like:
            return True
        if proximity_pattern and proximity_pattern.search(text) and not _document_is_multi_entity_listing(document):
            return True
    return False


def _documents_explicitly_support_employee_size(documents: list[EvidenceDocument]) -> bool:
    for document in documents:
        text = _document_text(document)
        if EMPLOYEE_VALUE_PATTERN.search(text) or EMPLOYEE_RANGE_VALUE_PATTERN.search(text):
            return True
        lowered = normalize_text(text)
        if "company size" in lowered or "employees" in lowered or "empleados" in lowered:
            return True
    return False


def derive_meddicc_signals(dossier: AssembledLeadDossier, request: NormalizedLeadSearchRequest) -> list[str]:
    text = " ".join([_document_text(item) for item in dossier.evidence]).lower()
    signals: list[str] = []
    if dossier.fit_signals:
        signals.append("identified_pain_or_priority")
    if any(term in text for term in ["pricing", "roi", "revenue", "cost", "efficiency", "time saved"]):
        signals.append("metrics_or_business_case")
    if dossier.person and dossier.person.role_title:
        signals.append("possible_champion_or_buyer")
    if any(term in text for term in ["compliance", "security", "integration", "workflow", "decision"]):
        signals.append("decision_criteria_context")
    _ = request
    return dedupe_preserve_order(signals)


def derive_score_from_rubric(rubric: QualificationRubric, dossier: AssembledLeadDossier, request: NormalizedLeadSearchRequest) -> int:
    score = 0
    for field in rubric.fields:
        if field.status == FieldEvidenceStatus.SATISFIED:
            score += 15
        elif field.status == FieldEvidenceStatus.WEAKLY_SUPPORTED:
            score += 8
        elif field.status == FieldEvidenceStatus.CONTRADICTED:
            score -= 15
    if dossier.fit_signals:
        score += min(len(dossier.fit_signals) * 6, 18)
    if dossier.person and dossier.person.full_name:
        score += 6
    if rubric.meddicc_signals:
        score += min(len(rubric.meddicc_signals) * 4, 12)
    _ = request
    return max(0, min(score, 100))


def build_qualification_rubric(dossier: AssembledLeadDossier, request: NormalizedLeadSearchRequest) -> QualificationRubric:
    fields = field_evidence_map(dossier)
    rubric_fields = [
        _rubric_field_from_field_evidence(fields.get("company_name"), field_name="company_name", fallback_note="Main company entity could not be resolved."),
        _rubric_field_from_field_evidence(fields.get("website"), field_name="website", fallback_note="Official website is not yet proven."),
        _rubric_field_from_field_evidence(fields.get("country"), field_name="country", fallback_note="Company geography is not yet proven."),
        _rubric_field_from_field_evidence(fields.get("employee_estimate"), field_name="employee_estimate", fallback_note="Company size is not yet proven."),
        _rubric_field_from_field_evidence(fields.get("person_name"), field_name="person_name", fallback_note="Named person is not yet proven."),
        _rubric_field_from_field_evidence(fields.get("role_title"), field_name="role_title", fallback_note="Role title is not yet proven."),
        _rubric_field_from_field_evidence(fields.get("fit_signals"), field_name="fit_signals", fallback_note="Fit signals are weak or absent."),
    ]
    meddicc = derive_meddicc_signals(dossier, request)
    contradictions = dedupe_preserve_order(dossier.contradictions)
    rubric = QualificationRubric(fields=rubric_fields, contradictions=contradictions, meddicc_signals=meddicc, overall_confidence=0)
    rubric.overall_confidence = derive_score_from_rubric(rubric, dossier, request)
    return rubric


def build_close_match_decision(
    *,
    score: int,
    reasons: list[str],
    lead_type: str,
    region: str,
    missed_filters: list[str],
    summary: str,
    qualification_rubric: QualificationRubric,
) -> QualificationDecision:
    unique_misses = dedupe_preserve_order(missed_filters)
    return QualificationDecision(
        outcome=QualificationOutcome.REJECT_CLOSE_MATCH,
        match_type=MatchType.CLOSE,
        score=score,
        summary=summary,
        reasons=reasons,
        type=lead_type,
        region=region,
        close_match=CloseMatch(summary="Commercially interesting candidate with exact-match gaps.", missed_filters=unique_misses or ["strict exact match"], reasons=reasons),
        qualification_rubric=qualification_rubric,
    )


def downgrade_enrich_to_close_match(
    decision: QualificationDecision,
    dossier: AssembledLeadDossier,
    request: NormalizedLeadSearchRequest,
) -> QualificationDecision:
    if decision.outcome != QualificationOutcome.ENRICH or decision.qualification_rubric is None:
        return decision
    rubric = decision.qualification_rubric
    field_map = {item.field_name: item for item in rubric.fields}
    company_field = field_map.get("company_name")
    website_field = field_map.get("website")
    country_field = field_map.get("country")
    if company_field is None or website_field is None or country_field is None:
        return decision
    if company_field.status != FieldEvidenceStatus.SATISFIED:
        return decision
    if website_field.status != FieldEvidenceStatus.SATISFIED:
        return decision
    if request.constraints.preferred_country and country_field.status != FieldEvidenceStatus.SATISFIED:
        return decision
    if decision.score < 60:
        return decision

    missed_filters: list[str] = []
    size_field = field_map.get("employee_estimate")
    person_field = field_map.get("person_name")
    role_field = field_map.get("role_title")
    if size_field and size_field.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        missed_filters.append("company_size")
    if request.constraints.prefer_named_person and person_field and person_field.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        missed_filters.append("named_person")
    role_candidate = dossier.person.role_title if dossier.person else None
    role_exact, role_adjacent = qualifies_role(role_candidate, request.buyer_targets)
    if not role_exact:
        missed_filters.append("preferred buyer persona" if role_adjacent else "preferred buyer persona")

    reasons = dedupe_preserve_order(
        [
            *decision.reasons,
            "available enrichment budget was exhausted before the remaining gaps could be fully verified",
        ]
    )
    summary = "Candidate remains commercially interesting, but some exact-match fields stayed unproven after the available enrichment budget."
    lead_type = dossier.person.role_title if dossier.person and dossier.person.role_title else decision.type or "unknown"
    region = dossier.company.country_code if dossier.company and dossier.company.country_code else decision.region or "unknown"
    return build_close_match_decision(
        score=decision.score,
        reasons=reasons,
        lead_type=lead_type,
        region=region,
        missed_filters=dedupe_preserve_order(missed_filters or ["strict exact match"]),
        summary=summary,
        qualification_rubric=rubric,
    )


def evaluate_dossier(dossier: AssembledLeadDossier, request: NormalizedLeadSearchRequest) -> QualificationDecision:
    if dossier.field_evidence == [] and dossier.evidence:
        dossier = overlay_explicit_dossier_fields(build_fallback_assembled_dossier(
            request=request,
            source_result=SourcePassResult(
                sourcing_status=dossier.sourcing_status,
                documents=dossier.evidence,
                anchored_company_name=dossier.company.name if dossier.company else None,
                notes=dossier.notes,
            ),
            prior_dossier=dossier,
        ), dossier)
    if dossier.sourcing_status != SourcingStatus.FOUND or dossier.company is None:
        return QualificationDecision(outcome=QualificationOutcome.REJECT, score=0, summary="No candidate dossier was assembled.", reasons=["sourcing and assembly did not produce a valid candidate"], qualification_rubric=QualificationRubric())

    rubric = build_qualification_rubric(dossier, request)
    field_map = {item.field_name: item for item in rubric.fields}
    reasons: list[str] = []
    missed_filters: list[str] = []
    hard_failure = False
    lead_type = dossier.person.role_title if dossier.person and dossier.person.role_title else "unknown"
    region = dossier.company.country_code or request.constraints.preferred_country or "unknown"

    country_field = field_map["country"]
    website_field = field_map["website"]
    size_field = field_map["employee_estimate"]
    person_field = field_map["person_name"]
    company_field = field_map["company_name"]
    role_field = field_map["role_title"]
    fit_field = field_map["fit_signals"]

    if website_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED} and (
        website_field.support_type == "weak_inference"
        or website_field.source_tier == "tier_c"
        or not _website_has_strong_support(dossier.company.website, website_field.supporting_evidence)
    ):
        _update_rubric_field(
            rubric,
            field_map,
            field_name="website",
            status=FieldEvidenceStatus.UNKNOWN,
            reasoning_note="Website is not sufficiently corroborated by company-controlled or matching-domain evidence.",
        )
        website_field = field_map["website"]

    if size_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED} and (
        size_field.support_type == "weak_inference"
        or size_field.source_tier == "tier_c"
        or not _documents_explicitly_support_employee_size(size_field.supporting_evidence)
    ):
        _update_rubric_field(
            rubric,
            field_map,
            field_name="employee_estimate",
            status=FieldEvidenceStatus.UNKNOWN,
            reasoning_note="Employee size was inferred without an explicit public size statement, so it is not yet proven.",
        )
        size_field = field_map["employee_estimate"]

    if person_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED} and (
        person_field.support_type == "weak_inference" or person_field.source_tier == "tier_c"
    ):
        _update_rubric_field(
            rubric,
            field_map,
            field_name="person_name",
            status=FieldEvidenceStatus.UNKNOWN,
            reasoning_note="Named person is not explicitly corroborated enough in the selected evidence.",
        )
        person_field = field_map["person_name"]

    if role_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED} and not _documents_explicitly_support_role(
        role_title=dossier.person.role_title if dossier.person else None,
        person_name=dossier.person.full_name if dossier.person else None,
        documents=role_field.supporting_evidence or dossier.evidence,
    ):
        _update_rubric_field(
            rubric,
            field_map,
            field_name="role_title",
            status=FieldEvidenceStatus.UNKNOWN,
            reasoning_note="Role title was inferred from leadership context but is not explicitly stated in the supporting evidence.",
        )
        role_field = field_map["role_title"]
    if role_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED} and (
        role_field.support_type == "weak_inference" or role_field.source_tier == "tier_c"
    ):
        _update_rubric_field(
            rubric,
            field_map,
            field_name="role_title",
            status=FieldEvidenceStatus.UNKNOWN,
            reasoning_note="Role title is not explicitly corroborated enough in the selected evidence.",
        )
        role_field = field_map["role_title"]

    role_candidate = dossier.person.role_title if dossier.person and role_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED} else None
    role_exact, role_adjacent = qualifies_role(role_candidate, request.buyer_targets)
    if role_exact:
        reasons.append("role matches the preferred buyer persona")
    elif role_adjacent:
        reasons.append("role is adjacent to the preferred buyer persona")
        missed_filters.append("preferred buyer persona")
    else:
        reasons.append("preferred buyer persona is still weakly supported or unknown")
        missed_filters.append("preferred buyer persona")

    if company_field.status == FieldEvidenceStatus.CONTRADICTED or domain_is_publisher_like(domain_from_url(dossier.company.website)):
        hard_failure = True
        reasons.append("main company entity is contradictory or still looks like a publisher/aggregator artifact")
    if website_field.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        reasons.append("official website is still weak or not fully corroborated")

    if request.constraints.preferred_country:
        if country_field.status == FieldEvidenceStatus.CONTRADICTED:
            hard_failure = True
            reasons.append("company geography contradicts the requested country")
        elif country_field.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
            reasons.append("company geography is not yet firmly proven")
        elif dossier.company.country_code != request.constraints.preferred_country:
            hard_failure = True
            reasons.append("company geography does not match the requested country")
        else:
            reasons.append("company geography matches the requested country")

    if request.constraints.min_company_size is not None or request.constraints.max_company_size is not None:
        size = dossier.company.employee_estimate
        if size_field.status == FieldEvidenceStatus.CONTRADICTED:
            hard_failure = True
            reasons.append("public evidence for company size is contradictory")
        elif size is None or size_field.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
            reasons.append("company size is not yet proven")
        else:
            minimum = request.constraints.min_company_size
            maximum = request.constraints.max_company_size
            if minimum is not None and size < minimum:
                hard_failure = True
                reasons.append("company size falls below the requested range")
            elif maximum is not None and size > maximum:
                hard_failure = True
                reasons.append("company size exceeds the requested range")
            else:
                reasons.append("company size fits the requested range")

    if person_field.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        reasons.append("named person is still weak or missing")
    elif dossier.person and dossier.person.full_name:
        reasons.append("named person is supported by the evidence")
    elif request.constraints.prefer_named_person:
        reasons.append("named person is not preserved in the final dossier")
        missed_filters.append("named person")

    if fit_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED} and dossier.fit_signals:
        reasons.append("company shows relevant automation or AI signals")
    else:
        reasons.append("fit signals remain weak")

    score = derive_score_from_rubric(rubric, dossier, request)
    hard_unknown = False
    if request.constraints.preferred_country and country_field.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        hard_unknown = True
    if (request.constraints.min_company_size is not None or request.constraints.max_company_size is not None) and size_field.status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        hard_unknown = True
    named_person_present = bool(dossier.person and dossier.person.full_name)
    role_title_present = bool(dossier.person and dossier.person.role_title)

    if hard_failure:
        return QualificationDecision(outcome=QualificationOutcome.REJECT, score=score, summary="Candidate fails a hard constraint or the company entity is unreliable.", reasons=dedupe_preserve_order(reasons + rubric.contradictions), type=lead_type, region=region, qualification_rubric=rubric)
    if hard_unknown:
        return QualificationDecision(outcome=QualificationOutcome.ENRICH, score=score, summary="Candidate is plausible but still needs stronger proof for one or more hard constraints.", reasons=dedupe_preserve_order(reasons), type=lead_type, region=region, qualification_rubric=rubric)
    if (
        role_exact
        and website_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED}
        and person_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED}
        and role_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED}
        and fit_field.status in {FieldEvidenceStatus.SATISFIED, FieldEvidenceStatus.WEAKLY_SUPPORTED}
        and (not request.constraints.prefer_named_person or (named_person_present and role_title_present))
    ):
        return QualificationDecision(outcome=QualificationOutcome.ACCEPT, match_type=MatchType.EXACT, score=score, summary="Candidate is a strong exact match backed by structured public evidence.", reasons=dedupe_preserve_order(reasons), type=lead_type, region=region, qualification_rubric=rubric)
    return build_close_match_decision(score=score, reasons=dedupe_preserve_order(reasons), lead_type=lead_type, region=region, missed_filters=missed_filters or ["exact buyer persona"], summary="Candidate is commercially interesting but still misses part of the preferred fit.", qualification_rubric=rubric)


def merge_qualification_decisions(deterministic: QualificationDecision, llm_review: QualificationDecision | None) -> QualificationDecision:
    if llm_review is None:
        return deterministic
    if deterministic.outcome == QualificationOutcome.REJECT:
        return deterministic
    if deterministic.outcome == QualificationOutcome.ENRICH and llm_review.outcome == QualificationOutcome.ACCEPT:
        return deterministic
    if deterministic.outcome == QualificationOutcome.REJECT_CLOSE_MATCH and llm_review.outcome == QualificationOutcome.ACCEPT:
        return deterministic
    merged = llm_review.model_copy(deep=True)
    if merged.qualification_rubric is None:
        merged.qualification_rubric = deterministic.qualification_rubric
    if merged.score == 0:
        merged.score = deterministic.score
    if deterministic.outcome == QualificationOutcome.ACCEPT and merged.outcome != QualificationOutcome.ACCEPT:
        merged.match_type = deterministic.match_type
    return merged


def collect_missing_fields_for_enrichment(dossier: AssembledLeadDossier, request: NormalizedLeadSearchRequest) -> list[str]:
    field_map = field_evidence_map(dossier)
    missing: list[str] = []
    if request.constraints.preferred_country and field_map.get("country", AssembledFieldEvidence(field_name="country", status=FieldEvidenceStatus.UNKNOWN, reasoning_note="country missing")).status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        missing.append("country")
    if (request.constraints.min_company_size is not None or request.constraints.max_company_size is not None) and field_map.get("employee_estimate", AssembledFieldEvidence(field_name="employee_estimate", status=FieldEvidenceStatus.UNKNOWN, reasoning_note="size missing")).status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        missing.append("employee_estimate")
    if field_map.get("person_name", AssembledFieldEvidence(field_name="person_name", status=FieldEvidenceStatus.UNKNOWN, reasoning_note="person missing")).status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        missing.append("person_name")
    if field_map.get("role_title", AssembledFieldEvidence(field_name="role_title", status=FieldEvidenceStatus.UNKNOWN, reasoning_note="role missing")).status in {FieldEvidenceStatus.UNKNOWN, FieldEvidenceStatus.WEAKLY_SUPPORTED}:
        missing.append("role_title")
    return dedupe_preserve_order(missing)


def build_fallback_commercial_bundle(
    dossier: AssembledLeadDossier,
    qualification: QualificationDecision,
    request: NormalizedLeadSearchRequest,
) -> CommercialBundle:
    person_name = dossier.person.full_name if dossier.person and dossier.person.full_name else "there"
    company_name = dossier.company.name if dossier.company else "your company"
    role_title = dossier.person.role_title if dossier.person and dossier.person.role_title else "your team"
    evidence_summary = "; ".join(f"{item.title} ({item.url})" for item in dossier.evidence[:3]) or "No explicit evidence captured."

    hooks = [
        f"{company_name} appears active around {', '.join(dossier.fit_signals[:2] or request.search_themes[:2])}.",
        f"The contact role aligns with {role_title}.",
    ]
    connection_note = (
        f"Hola {person_name}, he visto varias señales de que {company_name} está trabajando temas "
        f"de {', '.join(dossier.fit_signals[:2] or request.search_themes[:2])}. "
        "Yo ayudo a equipos IT a convertir esas iniciativas en automatizaciones y workflows agentic útiles."
    )
    dm_draft = (
        f"Hola {person_name}, estuve mirando {company_name} y creo que puede haber una oportunidad clara "
        "para acelerar procesos internos con GenAI y automatización aplicada. "
        "Si te encaja, te comparto 2 o 3 ideas muy concretas."
    )
    email_subject = f"Idea concreta de automatización para {company_name}"
    email_body = (
        f"Hola {person_name},\n\n"
        f"He revisado {company_name} y veo señales útiles en torno a {', '.join(dossier.fit_signals[:3] or request.search_themes[:3])}. "
        "Trabajo como freelancer de sistemas agentic y GenAI para equipos IT que quieren aterrizar mejoras reales, no solo pruebas.\n\n"
        "Si te interesa, puedo proponerte unas cuantas automatizaciones o flujos aplicables a vuestro contexto.\n\n"
        "Un saludo."
    )
    return CommercialBundle(
        source_notes=evidence_summary,
        hooks=hooks,
        fit_summary=qualification.summary,
        connection_note_draft=connection_note,
        dm_draft=dm_draft,
        email_subject=email_subject,
        email_body=email_body,
    )


def status_after_finish(has_accepted: bool, has_errors: bool, has_shortlist: bool) -> RunStatus:
    if has_accepted:
        return RunStatus.COMPLETED_WITH_ERRORS if has_errors else RunStatus.COMPLETED
    if has_shortlist:
        return RunStatus.COMPLETED_WITH_ERRORS if has_errors else RunStatus.COMPLETED
    return RunStatus.NO_RESULT if not has_errors else RunStatus.COMPLETED_WITH_ERRORS
