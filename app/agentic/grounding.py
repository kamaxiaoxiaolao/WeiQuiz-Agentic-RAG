"""Answer grounding / reflection for Agentic RAG.

The verifier checks whether final answer claims are supported by retrieved
evidence. It is intentionally evidence-driven: the model judges against source
nodes, not against its own confidence.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Sequence

from app.llm import LLMTask, get_llm_gateway
from app.metadata_schema import SourceNodePayload

logger = logging.getLogger(__name__)


MAX_EVIDENCE_CHARS = 9000


@dataclass
class ClaimJudgement:
    claim: str
    verdict: str
    evidence_ids: list[int] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "claim": self.claim,
            "verdict": self.verdict,
            "evidence_ids": self.evidence_ids,
            "reason": self.reason,
        }


@dataclass
class GroundingResult:
    verdict: str
    grounding_score: float
    summary: str
    claims: list[ClaimJudgement] = field(default_factory=list)
    unsupported_points: list[str] = field(default_factory=list)
    method: str = "llm_claim_judge"
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "grounding_score": self.grounding_score,
            "summary": self.summary,
            "claims": [claim.to_dict() for claim in self.claims],
            "unsupported_points": self.unsupported_points,
            "method": self.method,
            "error": self.error,
        }


GROUNDING_PROMPT = """You are the verifier layer for an enterprise Agentic RAG system.

Your task is to verify whether the final answer is supported by the retrieved
evidence. Do not use outside knowledge. If a claim is reasonable but not stated
or implied by evidence, mark it unsupported.

Allowed verdicts for each claim:
- supported: evidence clearly supports the claim
- partial: evidence partially supports the claim but misses important details
- unsupported: evidence does not support the claim
- contradicted: evidence conflicts with the claim

Return strict JSON only:
{{
  "claims": [
    {{
      "claim": "short factual claim",
      "verdict": "supported|partial|unsupported|contradicted",
      "evidence_ids": [1],
      "reason": "brief reason"
    }}
  ],
  "summary": "brief overall judgement"
}}

User question:
{question}

Final answer:
{answer}

Retrieved evidence:
{evidence}
"""


def should_run_grounding(
    *,
    answer: str,
    nodes: Sequence,
    route: dict | None = None,
    quality: dict | None = None,
) -> bool:
    """Decide whether to run the verifier.

    First version is deliberately broad for knowledge answers: if we have an
    answer and evidence, run grounding. Chitchat and empty-evidence answers skip.
    """

    if not answer.strip() or not nodes:
        return False
    intent = str((route or {}).get("intent") or "")
    if intent == "chitchat":
        return False
    if (quality or {}).get("quality") == "bad":
        return True
    return True


def check_answer_grounding(
    *,
    question: str,
    answer: str,
    nodes: Sequence,
    max_evidence_chars: int = MAX_EVIDENCE_CHARS,
) -> GroundingResult:
    """Run claim-level grounding check against retrieved evidence."""

    if not answer.strip():
        return GroundingResult(
            verdict="fail",
            grounding_score=0.0,
            summary="答案为空，无法校验。",
            method="empty_answer",
        )
    if not nodes:
        return GroundingResult(
            verdict="fail",
            grounding_score=0.0,
            summary="没有可用检索证据，无法支撑答案。",
            unsupported_points=[answer[:200]],
            method="no_evidence",
        )

    evidence = _format_evidence(nodes, max_chars=max_evidence_chars)
    try:
        response = get_llm_gateway().chat_completion(
            task=LLMTask.GROUNDING,
            messages=[
                {"role": "system", "content": "你是企业 RAG 系统的证据一致性校验器。"},
                {
                    "role": "user",
                    "content": GROUNDING_PROMPT.format(
                        question=question,
                        answer=answer,
                        evidence=evidence,
                    ),
                },
            ],
            temperature=0,
        )
        payload = _parse_json(response.choices[0].message.content or "")
        return _result_from_payload(payload)
    except Exception as exc:
        logger.warning("Grounding check failed: %s", exc)
        return GroundingResult(
            verdict="warning",
            grounding_score=0.0,
            summary="Grounding 校验调用失败，保留原回答但标记为未校验。",
            method="llm_claim_judge_failed",
            error=str(exc),
        )


def _format_evidence(nodes: Sequence, *, max_chars: int) -> str:
    lines: list[str] = []
    used = 0
    for idx, node in enumerate(nodes, start=1):
        payload = SourceNodePayload.from_node(node)
        text = payload.text.strip()
        if not text:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining]
        label = payload.file_name or payload.doc_id or payload.source_path or "unknown_source"
        lines.append(f"[{idx}] {label}\n{text}")
        used += len(text)
    return "\n\n".join(lines)


def _result_from_payload(payload: dict) -> GroundingResult:
    claims: list[ClaimJudgement] = []
    for item in payload.get("claims") or []:
        if not isinstance(item, dict):
            continue
        verdict = str(item.get("verdict") or "unsupported").strip().lower()
        if verdict not in {"supported", "partial", "unsupported", "contradicted"}:
            verdict = "unsupported"
        evidence_ids = item.get("evidence_ids") or []
        if not isinstance(evidence_ids, list):
            evidence_ids = []
        claims.append(
            ClaimJudgement(
                claim=str(item.get("claim") or "").strip(),
                verdict=verdict,
                evidence_ids=[int(value) for value in evidence_ids if str(value).isdigit()],
                reason=str(item.get("reason") or "").strip(),
            )
        )

    score = _score_claims(claims)
    unsupported = [
        claim.claim
        for claim in claims
        if claim.verdict in {"unsupported", "contradicted"} and claim.claim
    ]
    return GroundingResult(
        verdict=_verdict_from_score(score, claims),
        grounding_score=score,
        summary=str(payload.get("summary") or "").strip() or _summary_from_score(score),
        claims=claims,
        unsupported_points=unsupported,
    )


def _score_claims(claims: list[ClaimJudgement]) -> float:
    if not claims:
        return 0.0
    weights = {
        "supported": 1.0,
        "partial": 0.5,
        "unsupported": 0.0,
        "contradicted": 0.0,
    }
    total = sum(weights.get(claim.verdict, 0.0) for claim in claims)
    return round(total / len(claims), 4)


def _verdict_from_score(score: float, claims: list[ClaimJudgement]) -> str:
    if any(claim.verdict == "contradicted" for claim in claims):
        return "fail"
    if score >= 0.8:
        return "pass"
    if score >= 0.5:
        return "warning"
    return "fail"


def _summary_from_score(score: float) -> str:
    if score >= 0.8:
        return "答案大部分事实声明能被检索证据支撑。"
    if score >= 0.5:
        return "答案部分内容有证据支撑，但存在证据不足的声明。"
    return "答案中较多事实声明缺少检索证据支撑。"


def _parse_json(content: str) -> dict:
    content = content.strip()
    if "```" in content:
        match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
        if match:
            content = match.group(1).strip()
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1:
        content = content[start : end + 1]
    return json.loads(content)
