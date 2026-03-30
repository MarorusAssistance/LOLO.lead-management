from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

from lolo_lead_management.domain.enums import MatchType, PlannerAction, QualificationOutcome, RunStatus, SourcingStatus
from lolo_lead_management.domain.models import (
    CloseMatch,
    CommercialBundle,
    CompanyCandidate,
    EvidenceItem,
    LeadSearchConstraints,
    NormalizedLeadSearchRequest,
    PersonCandidate,
    QualificationDecision,
    SearchBudget,
    SourcingDossier,
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
    "ai engineering": "ai engineering",
    "automation": "automation",
    "workflow": "agentic workflows",
    "agentic": "agentic workflows",
    "software": "software company",
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

GENERIC_DIRECTORY_DOMAINS = {
    "apollo.io",
    "crunchbase.com",
    "designrush.com",
    "eu-startups.com",
    "f6s.com",
    "linkedin.com",
    "rocketreach.co",
    "seedtable.com",
    "startupstash.com",
    "techbehemoths.com",
    "tracxn.com",
    "wellfound.com",
}

ROLE_PATTERN = re.compile(
    r"\b(ceo|founder|cofounder|cto|chief technology officer|head of engineering|vp engineering|engineering manager|technical recruiter|talent lead|head of talent|recruiter)\b",
    re.IGNORECASE,
)
COUNT_PATTERN = re.compile(r"\b(?:find|search|busca|buscar)?\s*(\d+)\s+leads?\b", re.IGNORECASE)
SIZE_RANGE_PATTERN = re.compile(r"(?:entre|between)\s*(\d+)\s*(?:y|and|-)\s*(\d+)\s*(?:empleados|employees)", re.IGNORECASE)
SIZE_MIN_PATTERN = re.compile(r"(?:more than|over|mas de|más de)\s*(\d+)\s*(?:empleados|employees)", re.IGNORECASE)
SIZE_MAX_PATTERN = re.compile(r"(?:less than|under|menos de)\s*(\d+)\s*(?:empleados|employees)", re.IGNORECASE)
EMPLOYEE_VALUE_PATTERN = re.compile(r"(?:employees|empleados)\s*[:\-]?\s*(\d+)", re.IGNORECASE)
COUNTRY_PATTERN = re.compile(r"(?:country|pais|país)\s*[:\-]?\s*([A-Za-zñáéíóú ]+)", re.IGNORECASE)
PERSON_PATTERN = re.compile(r"(?:person|persona|contact)\s*[:\-]?\s*([^\n|]+)", re.IGNORECASE)
ROLE_VALUE_PATTERN = re.compile(r"(?:role|puesto|title|cargo)\s*[:\-]?\s*([^\n|]+)", re.IGNORECASE)
COMPANY_PATTERN = re.compile(r"(?:company|empresa)\s*[:\-]?\s*([^\n|]+)", re.IGNORECASE)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


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
        canonical = BUYER_ALIASES.get(normalized)
        if canonical is None:
            canonical = BUYER_ALIASES.get(normalized.replace("_", " "))
        if canonical and canonical not in output:
            output.append(canonical)
    return output


def canonicalize_search_themes(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        canonical = THEME_ALIASES.get(normalized)
        if normalized == "llm":
            canonical = "genai"
        elif normalized == "machine learning":
            canonical = "ai engineering"
        elif normalized == "workflow":
            canonical = "automation"
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
    if "llm" in normalized and "genai" not in themes:
        themes.append("genai")
    if "machine learning" in normalized and "ai engineering" not in themes:
        themes.append("ai engineering")
    return themes


def build_constraints(text: str) -> LeadSearchConstraints:
    minimum, maximum = extract_company_size(text)
    preferred_country = extract_country_code(text)
    hard_constraints: list[str] = []
    relaxable_constraints: list[str] = []
    if preferred_country:
        hard_constraints.append("preferred_country")
    if minimum is not None or maximum is not None:
        hard_constraints.append("company_size")
    relaxable_constraints.append("named_person")
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
    constraints.relaxable_constraints = dedupe_preserve_order(
        constraints.relaxable_constraints or baseline.constraints.relaxable_constraints
    )
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


def build_query_candidates(request: NormalizedLeadSearchRequest, relaxation_stage: int) -> list[str]:
    country_terms = COUNTRY_QUERY_TERMS.get(request.constraints.preferred_country or "es", ["Spain", "Madrid", "Barcelona"])
    broad_country = country_terms[0]
    city_terms = [term for term in country_terms if term in {"Madrid", "Barcelona", "Valencia", "Lisbon", "Paris", "Berlin", "London"}]
    size_hint = []
    if request.constraints.max_company_size is not None:
        size_hint.append(f'under {request.constraints.max_company_size} employees')
    elif request.constraints.min_company_size is not None:
        size_hint.append(f'over {request.constraints.min_company_size} employees')

    buyers = [item.replace("_", " ") for item in (request.buyer_targets or DEFAULT_BUYER_TARGETS)[:3]]
    themes = request.search_themes[:]
    if relaxation_stage >= 1 and "software company" not in themes:
        themes.append("software company")
    if relaxation_stage >= 2 and "automation" not in themes:
        themes.append("automation")

    theme_terms: list[str] = []
    for theme in themes[:3]:
        theme_terms.extend(THEME_QUERY_TERMS.get(theme, [theme]))
    theme_terms = dedupe_preserve_order(theme_terms or ["AI", "automation", "software company"])

    queries: list[str] = []
    for theme in theme_terms[:3]:
        queries.append(f"site:eu-startups.com/directory/ {broad_country} {theme} software")
        queries.append(f"{broad_country} startup {theme} software company")
        if size_hint:
            queries.append(f"{broad_country} startup {theme} {size_hint[0]}")
    for city in city_terms[:2]:
        queries.append(f"site:seedtable.com best AI startups in {city}")
        queries.append(f"{city} B2B software startup automation")
    for buyer in buyers[:2]:
        for theme in theme_terms[:2]:
            queries.append(f"{broad_country} {buyer} {theme} software startup")
    if relaxation_stage >= 1:
        queries.extend(
            [
                f"site:eu-startups.com {broad_country} startup automation",
                f"site:startupstash.com {broad_country} startup AI",
                f"{broad_country} AI startup founder software",
            ]
        )
    if relaxation_stage >= 2:
        queries.extend(
            [
                f"{broad_country} software startup AI",
                f"{broad_country} SaaS automation startup",
                f"site:techbehemoths.com artificial intelligence companies {broad_country}",
            ]
        )
    return dedupe_preserve_order(queries)


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


def choose_query(candidates: list[str], query_history: list[str]) -> str | None:
    used = {normalize_text(item) for item in query_history}
    for candidate in candidates:
        if normalize_text(candidate) not in used:
            return candidate
    return None


def clean_company_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[_\-]+", " ", value)
    cleaned = re.sub(r"[^A-Za-z0-9.& ]+", " ", cleaned)
    cleaned = re.sub(r"\b(page|directory|startups?|companies?)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|")
    if not cleaned or len(cleaned) < 2:
        return None
    return cleaned.title() if cleaned.islower() else cleaned.strip()


def extract_domain_company_name(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").removeprefix("www.")
    if not hostname:
        return None
    if hostname in GENERIC_DIRECTORY_DOMAINS:
        parts = [segment for segment in parsed.path.split("/") if segment]
        if "directory" in parts:
            index = parts.index("directory")
            if index + 1 < len(parts):
                return clean_company_name(parts[index + 1])
        if parts:
            return clean_company_name(parts[-1])
        return None
    return clean_company_name(hostname.split(".")[0])


def title_company_name(title: str) -> str | None:
    title = title.strip()
    if not title:
        return None
    parts = re.split(r"\s+[|\-]\s+", title, maxsplit=1)
    primary = parts[0].strip()
    if re.search(r"\b(startups?|companies?|jobs?|rankings?)\b", primary, re.IGNORECASE):
        return None
    return clean_company_name(primary)


def extract_official_website(text: str, source_url: str) -> str | None:
    urls = re.findall(r"https?://[^\s)]+", text, flags=re.IGNORECASE)
    try:
        source_host = (urlparse(source_url).hostname or "").removeprefix("www.")
    except ValueError:
        source_host = ""
    for candidate in urls:
        try:
            hostname = (urlparse(candidate).hostname or "").removeprefix("www.")
        except ValueError:
            continue
        if not hostname or hostname == source_host or hostname in GENERIC_DIRECTORY_DOMAINS:
            continue
        return candidate.rstrip(".,)")
    if source_host and source_host not in GENERIC_DIRECTORY_DOMAINS:
        return source_url
    return None


def parse_candidate_from_text(text: str, url: str) -> tuple[PersonCandidate | None, CompanyCandidate | None]:
    person_name = PERSON_PATTERN.search(text).group(1).strip() if PERSON_PATTERN.search(text) else None
    role_title = ROLE_VALUE_PATTERN.search(text).group(1).strip() if ROLE_VALUE_PATTERN.search(text) else None
    if role_title is None:
        for match in ROLE_PATTERN.finditer(text):
            role_title = match.group(1)
            break
    company_name = clean_company_name(COMPANY_PATTERN.search(text).group(1)) if COMPANY_PATTERN.search(text) else None
    country_code = extract_country_code(COUNTRY_PATTERN.search(text).group(1)) if COUNTRY_PATTERN.search(text) else extract_country_code(text)
    employee_estimate = int(EMPLOYEE_VALUE_PATTERN.search(text).group(1)) if EMPLOYEE_VALUE_PATTERN.search(text) else None
    if company_name is None:
        company_name = extract_domain_company_name(url)
    if company_name and (len(company_name.split()) > 8 or len(company_name) > 80):
        company_name = extract_domain_company_name(url) or title_company_name(text.splitlines()[0] if text else "")
    person = PersonCandidate(full_name=person_name, role_title=role_title) if person_name or role_title else None
    company = (
        CompanyCandidate(
            name=company_name,
            website=extract_official_website(text, url),
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


def score_candidate(
    *,
    request: NormalizedLeadSearchRequest,
    person: PersonCandidate | None,
    company: CompanyCandidate | None,
    fit_signals: list[str],
    evidence_count: int,
) -> int:
    if company is None:
        return -100
    score = 20
    role_exact, role_adjacent = qualifies_role(person.role_title if person else None, request.buyer_targets)
    if role_exact:
        score += 20
    elif role_adjacent:
        score += 10
    if request.constraints.preferred_country and company.country_code == request.constraints.preferred_country:
        score += 15
    if company.employee_estimate is not None:
        score += 10
    if fit_signals:
        score += min(len(fit_signals) * 8, 16)
    score += min(evidence_count * 4, 12)
    if person and person.full_name:
        score += 8
    return score


def choose_best_evidence_item(items: list[EvidenceItem], request: NormalizedLeadSearchRequest) -> EvidenceItem | None:
    scored: list[tuple[int, EvidenceItem]] = []
    for item in items:
        combined = f"{item.title} {item.snippet}"
        person, company = parse_candidate_from_text(combined, item.url)
        fit_signals = collect_fit_signals(combined, request)
        scored.append(
            (
                score_candidate(
                    request=request,
                    person=person,
                    company=company,
                    fit_signals=fit_signals,
                    evidence_count=1,
                ),
                item,
            )
        )
    if not scored:
        return None
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored[0][1]


def build_heuristic_dossier(
    *,
    request: NormalizedLeadSearchRequest,
    query: str,
    evidence_items: list[EvidenceItem],
    page_texts: dict[str, str],
) -> SourcingDossier:
    selected = choose_best_evidence_item(evidence_items, request)
    if selected is None:
        return SourcingDossier(
            sourcing_status=SourcingStatus.NO_CANDIDATE,
            query_used=query,
            notes=["no_candidate_from_search_results"],
        )

    combined_text = " ".join([selected.title, selected.snippet, page_texts.get(selected.url, "")])
    person, company = parse_candidate_from_text(combined_text, selected.url)
    if company is None and (title_name := title_company_name(selected.title)):
        company = CompanyCandidate(
            name=title_name,
            website=extract_official_website(page_texts.get(selected.url, ""), selected.url),
            country_code=extract_country_code(combined_text),
            employee_estimate=None,
        )
    if company is None:
        return SourcingDossier(
            sourcing_status=SourcingStatus.NO_CANDIDATE,
            query_used=query,
            evidence=evidence_items[:2],
            notes=["unable_to_extract_company"],
        )

    fit_signals = collect_fit_signals(
        " ".join([item.title + " " + item.snippet for item in evidence_items[:2]]) + " " + combined_text,
        request,
    )
    supporting_evidence = [selected] + [item for item in evidence_items if item.url != selected.url][:1]
    return SourcingDossier(
        sourcing_status=SourcingStatus.FOUND,
        query_used=query,
        person=person,
        company=company,
        fit_signals=fit_signals,
        evidence=supporting_evidence,
        notes=[f"query_used={query}", "candidate_selected_by=heuristic"],
    )


def decide_planner_action(*, accepted_count: int, target_count: int, budget: SearchBudget, shortlist_count: int) -> StageDecision:
    if accepted_count >= target_count:
        return StageDecision(action=PlannerAction.FINISH_ACCEPTED, relaxation_stage=relaxation_stage_from_budget(budget), reason="target_count_reached")
    if not budget.can_source():
        if shortlist_count:
            return StageDecision(action=PlannerAction.FINISH_SHORTLIST, relaxation_stage=relaxation_stage_from_budget(budget), reason="budget_exhausted_with_shortlist")
        return StageDecision(action=PlannerAction.FINISH_NO_RESULT, relaxation_stage=relaxation_stage_from_budget(budget), reason="budget_exhausted_without_results")
    return StageDecision(action=PlannerAction.SOURCE, relaxation_stage=relaxation_stage_from_budget(budget), reason="continue_sourcing")


def qualifies_role(role_title: str | None, buyer_targets: list[str]) -> tuple[bool, bool]:
    if role_title is None:
        return False, False
    normalized = normalize_text(role_title)
    exact = any(target.replace("_", " ") in normalized for target in buyer_targets)
    adjacent = any(keyword in normalized for keyword in ["engineering", "founder", "technology", "talent", "recruit"])
    return exact, adjacent


def build_close_match_decision(
    *,
    score: int,
    reasons: list[str],
    lead_type: str,
    region: str,
    missed_filters: list[str],
    summary: str,
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
        close_match=CloseMatch(
            summary="Commercially interesting candidate with exact-match gaps.",
            missed_filters=unique_misses or ["strict exact match"],
            reasons=reasons,
        ),
    )


def merge_qualification_decisions(
    deterministic: QualificationDecision,
    llm_review: QualificationDecision | None,
) -> QualificationDecision:
    if llm_review is None:
        return deterministic
    merged = deterministic.model_copy(deep=True)
    if llm_review.outcome == deterministic.outcome:
        if llm_review.summary:
            merged.summary = llm_review.summary
        merged.type = merged.type or llm_review.type
        merged.region = merged.region or llm_review.region
        if merged.outcome == QualificationOutcome.REJECT_CLOSE_MATCH and merged.close_match and llm_review.close_match is not None:
            merged.close_match.summary = llm_review.close_match.summary or merged.close_match.summary
    else:
        merged.reasons = dedupe_preserve_order([*deterministic.reasons, f"llm_review_disagreed={llm_review.outcome.value}"])
    return merged


def evaluate_dossier(dossier: SourcingDossier, request: NormalizedLeadSearchRequest) -> QualificationDecision:
    if dossier.sourcing_status != SourcingStatus.FOUND or dossier.company is None:
        return QualificationDecision(
            outcome=QualificationOutcome.REJECT,
            score=0,
            summary="No candidate dossier was found.",
            reasons=["sourcing did not produce a valid candidate"],
        )

    reasons: list[str] = []
    score = 0
    hard_misses: list[str] = []
    hard_unknowns: list[str] = []
    close_misses: list[str] = []

    role_exact, role_adjacent = qualifies_role(dossier.person.role_title if dossier.person else None, request.buyer_targets)
    if role_exact:
        score += 30
        reasons.append("buyer persona matches the preferred targets")
    elif role_adjacent:
        score += 15
        close_misses.append("preferred buyer persona")
        reasons.append("role is adjacent to the preferred buyer persona")
    else:
        close_misses.append("preferred buyer persona")
        reasons.append("preferred buyer persona is not yet fully matched")

    country = request.constraints.preferred_country
    if country:
        if dossier.company.country_code == country:
            score += 20
            reasons.append("company geography matches the requested country")
        elif dossier.company.country_code:
            hard_misses.append("preferred_country")
        else:
            hard_unknowns.append("preferred_country")
            reasons.append("company geography is not fully evidenced yet")

    if request.constraints.min_company_size is not None or request.constraints.max_company_size is not None:
        size = dossier.company.employee_estimate
        if size is None:
            hard_unknowns.append("company_size")
            reasons.append("company size is not fully evidenced yet")
        else:
            minimum = request.constraints.min_company_size
            maximum = request.constraints.max_company_size
            if minimum is not None and size < minimum:
                hard_misses.append("company_size")
            elif maximum is not None and size > maximum:
                hard_misses.append("company_size")
            else:
                score += 20
                reasons.append("company size falls within the requested range")

    if dossier.fit_signals:
        score += min(len(dossier.fit_signals) * 10, 20)
        reasons.append("company shows relevant automation or AI signals")

    if dossier.person and dossier.person.full_name:
        score += 5
        reasons.append("named person found")

    evidence_count = len(dossier.evidence)
    if evidence_count >= 2:
        score += 15
        reasons.append("evidence is supported by multiple URLs")
    elif evidence_count == 1:
        score += 5
        reasons.append("only one supporting URL is available")

    score = min(score, 100)
    region = dossier.company.country_code or request.constraints.preferred_country or "unknown"
    lead_type = dossier.person.role_title if dossier.person and dossier.person.role_title else "unknown"

    if hard_misses:
        return QualificationDecision(
            outcome=QualificationOutcome.REJECT,
            score=score,
            summary="Candidate fails at least one hard constraint.",
            reasons=reasons + [f"hard miss: {item}" for item in hard_misses],
            type=lead_type,
            region=region,
        )

    if evidence_count < 2 and score >= 35:
        return QualificationDecision(
            outcome=QualificationOutcome.ENRICH,
            score=score,
            summary="Candidate looks promising but needs more evidence.",
            reasons=reasons + [f"missing evidence: {item}" for item in dedupe_preserve_order(hard_unknowns)],
            type=lead_type,
            region=region,
        )

    if hard_unknowns:
        if score >= 55:
            return build_close_match_decision(
                score=score,
                reasons=reasons,
                lead_type=lead_type,
                region=region,
                missed_filters=[*close_misses, *hard_unknowns],
                summary="Candidate is commercially interesting but cannot be confirmed as an exact match.",
            )
        return QualificationDecision(
            outcome=QualificationOutcome.REJECT,
            score=score,
            summary="Candidate lacks proof for one or more hard constraints.",
            reasons=reasons + [f"missing hard proof: {item}" for item in dedupe_preserve_order(hard_unknowns)],
            type=lead_type,
            region=region,
        )

    if score >= 70 and not close_misses:
        return QualificationDecision(
            outcome=QualificationOutcome.ACCEPT,
            match_type=MatchType.EXACT,
            score=score,
            summary="Candidate is a strong exact match.",
            reasons=reasons,
            type=lead_type,
            region=region,
        )

    if score >= 55:
        return build_close_match_decision(
            score=score,
            reasons=reasons,
            lead_type=lead_type,
            region=region,
            missed_filters=close_misses,
            summary="Candidate is commercially interesting but misses part of the preferred fit.",
        )

    return QualificationDecision(
        outcome=QualificationOutcome.REJECT,
        score=score,
        summary="Candidate is not strong enough for this request.",
        reasons=reasons or ["not enough relevant fit signals"],
        type=lead_type,
        region=region,
    )


def build_fallback_commercial_bundle(
    dossier: SourcingDossier,
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
