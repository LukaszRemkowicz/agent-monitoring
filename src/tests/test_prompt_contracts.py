from agents import (
    LOG_ANALYSIS_COMPARE_HISTORY_PROMPT,
    LOG_ANALYSIS_CRITICAL_DECISION_RULES,
    LOG_ANALYSIS_DECISION_SKILL,
    LOG_ANALYSIS_INSTRUCTIONS,
    LOG_ANALYSIS_NO_COMPARE_HISTORY_PROMPT,
)

ATTENTION_ORDER = ["actionable", "investigate", "watch_only", "routine"]


def test_mode_contracts_make_current_evidence_primary_and_history_secondary() -> None:
    for mode_prompt in (
        LOG_ANALYSIS_COMPARE_HISTORY_PROMPT,
        LOG_ANALYSIS_NO_COMPARE_HISTORY_PROMPT,
    ):
        evidence_contract = mode_prompt["evidence_contract"]

        assert "primary" in evidence_contract["current_grouped_errors"].lower()
        assert "paginated" in evidence_contract["current_grouped_errors"].lower()
        current_contract = evidence_contract["current_grouped_errors"].lower()
        assert "all semantic family identities" in current_contract
        assert "bounded per attention band" in current_contract
        assert "omitted_details" in current_contract
        assert "secondary" in evidence_contract["history"].lower()
        assert mode_prompt["attention_order"] == ATTENTION_ORDER


def test_central_decision_contract_defines_priority_probe_and_completeness_rules() -> None:
    central_contract = LOG_ANALYSIS_CRITICAL_DECISION_RULES.lower()

    assert "current complete paginated evidence is primary" in central_contract
    assert "historical summaries" in central_contract
    assert "secondary" in central_contract
    assert "all grouped semantic families" in central_contract
    assert "actionable, investigate, watch_only, routine" in central_contract
    assert "/wp-login.php" in central_contract
    assert "403/404" in central_contract
    assert "watch_only" in central_contract
    assert "count-only" in central_contract
    assert "outcome, severity, scope, or impact" in central_contract
    assert "current_grouped_errors.evidence_complete=false" in central_contract
    assert "final_report" in central_contract
    assert "prohibited" in central_contract
    assert "more current evidence" in central_contract
    assert "fail" in central_contract

    decision_skill = LOG_ANALYSIS_DECISION_SKILL.lower()
    assert "critical decision rules" in decision_skill
    assert "read_skills" in decision_skill
    assert "bot_detection" in decision_skill
    assert "owasp_security" in decision_skill
    assert "not evidence until it has been retrieved" in decision_skill
    assert any("critical decision rules" in rule.lower() for rule in LOG_ANALYSIS_INSTRUCTIONS)
