"""Check that the baseline measures failures without granting unreviewed quality passes."""

import io
import json
from urllib.error import URLError

import pytest
from pydantic import ValidationError

from evals.report_baseline import CASES_PATH, Suite, evaluate_case, main, status, summary
from financial_research import ollama_model
from financial_research.models import ResearchReport


@pytest.fixture
def suite():
    return Suite.model_validate_json(CASES_PATH.read_bytes())


def _report(evidence_id="E001"):
    return ResearchReport.model_validate({
        "title": "Price findings",
        "claims": [{"statement": "The price rose 999%.", "kind": "observation", "evidence_ids": [evidence_id]}],
        "limitations": ["Endpoint changes cannot establish causation."],
    })


def test_valid_citations_do_not_award_a_semantic_quality_pass(suite):
    result = evaluate_case(suite, suite.cases[0], lambda state: _report())
    assert all(result["automatic_checks"].values())
    assert status(result) == "PENDING"  # The deliberately incorrect 999% needs semantic grading.
    result["manual_checks"][0].update(passed=False, notes="Expected approximately 233.50%, not 999%.")
    assert status(result) == "FAIL"


def test_unknown_citation_cannot_be_overridden_by_manual_passes(suite):
    result = evaluate_case(suite, suite.cases[0], lambda state: _report("E999"))
    assert result["automatic_checks"]["citation_ids_exist"] is False
    assert result["report"]["claims"][0]["evidence_ids"] == ["E999"]
    for check in result["manual_checks"]:
        check["passed"] = True
    assert status(result) == "FAIL"


def test_claims_without_evidence_fail(suite):
    case = next(case for case in suite.cases if case.id == "no-evidence")
    result = evaluate_case(suite, case, lambda state: _report())
    assert result["automatic_checks"]["no_claims_without_evidence"] is False
    assert status(result) == "FAIL"


def test_cases_are_isolated_from_writer_mutations(suite):
    def mutate(state):
        state.evidence[0].content = "Mutated by an experimental writer."
        return _report()

    result = evaluate_case(suite, suite.cases[0], mutate)
    assert "Mutated" not in suite.evidence[0].content
    assert "Mutated" not in result["input_state"]["evidence"][0]["content"]


def test_suite_rejects_unknown_references_and_duplicate_ids(suite):
    raw = suite.model_dump(mode="json")
    raw["cases"][0]["evidence_ids"] = ["E999"]
    with pytest.raises(ValidationError, match="unknown evidence"):
        Suite.model_validate(raw)
    raw["cases"][0]["evidence_ids"] = ["E001"]
    raw["cases"].append(raw["cases"][0])
    with pytest.raises(ValidationError, match="unique"):
        Suite.model_validate(raw)


def test_local_run_preserves_failures_continues_and_can_be_manually_summarized(monkeypatch, tmp_path, capsys):
    requests = []

    def respond(request, timeout):
        assert request.full_url == ollama_model.URL  # No Yahoo, FRED, or hosted-model requests.
        payload = json.loads(request.data)
        assert payload["model"] == "local-test-model"
        assert "Write a short report" in payload["messages"][0]["content"]
        requests.append(payload)
        if len(requests) == 1:
            raise URLError("Connection refused")
        report = _report("E002").model_dump()
        return io.BytesIO(json.dumps({
            "message": {"content": json.dumps(report)}, "done": True, "done_reason": "stop",
        }).encode())

    monkeypatch.setattr(ollama_model, "urlopen", respond)
    assert main(["run", "--case", "price-facts", "--case", "yield-units", "--model", "local-test-model",
                 "--output", str(tmp_path)]) == 1
    output = next(tmp_path.iterdir())
    path = output / "results.json"
    record = json.loads(path.read_text())
    assert len(record["results"]) == 2
    assert record["model"] == "local-test-model" and record["cases_sha256"]
    failed, pending = record["results"]
    assert status(failed) == "ERROR" and "Cannot reach Ollama" in failed["error"]
    assert status(pending) == "PENDING"
    assert (output / "yield-units-1.md").exists()
    assert "1 pending / 1 error" in (output / "summary.md").read_text()
    for check in pending["manual_checks"]:
        check["passed"] = False  # The stub's price report does not answer the yield question.
    path.write_text(json.dumps(record))
    capsys.readouterr()
    assert main(["summarize", str(path)]) == 0
    assert "1 fail / 0 pending / 1 error" in capsys.readouterr().out
    assert len(requests) == 2  # Summarizing does not call the model again.


def test_repeats_keep_the_same_input_and_distinct_results(suite):
    first = evaluate_case(suite, suite.cases[0], lambda state: _report())
    second = evaluate_case(suite, suite.cases[0], lambda state: _report())
    assert first["input_state"] == second["input_state"]
    for repeat, result in enumerate((first, second), 1):
        result["repeat"] = repeat
        for check in result["manual_checks"]:
            check["passed"] = True
    assert "2 pass / 0 fail / 0 pending / 0 error" in summary([first, second])


def test_malformed_manual_grades_are_not_treated_as_passes(suite):
    result = evaluate_case(suite, suite.cases[0], lambda state: _report())
    result["manual_checks"][0]["passed"] = "false"
    with pytest.raises(ValueError, match="JSON true, false, or null"):
        status(result)
