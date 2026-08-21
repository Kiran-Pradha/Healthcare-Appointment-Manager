"""
All LLM calls live in this one file, isolated from the booking/notification
logic. That isolation is deliberate: if Anthropic's API is down, slow, or
returns malformed output, booking and notifications must keep working —
only the summary fields should degrade. Every public function here returns
a dict with a `failed` flag instead of raising, so callers never need a
try/except of their own; they just check `result['failed']` and show a
fallback message.
"""

import json
import logging
from anthropic import Anthropic, APIError, APITimeoutError
from django.conf import settings

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            return None
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


PRE_VISIT_PROMPT = """Analyse these patient-reported symptoms and respond with ONLY a JSON object \
(no markdown fences, no preamble) with exactly these keys:
- "urgency_level": one of "Low", "Medium", "High"
- "chief_complaint": a short (under 15 words) plain-language summary of the main issue
- "suggested_questions": a list of exactly 3 short questions the doctor should consider asking

Symptoms: {symptoms}"""

POST_VISIT_PROMPT = """Convert these clinical notes and prescription into a warm, plain-language \
summary a patient (non-medical background) can understand. Include:
- What the diagnosis / assessment means in simple terms
- A clear medication schedule (drug, dose, how often, for how long)
- Any follow-up steps or warning signs to watch for

Write it as friendly prose, not a bulleted clinical report. Keep it under 200 words.

Clinical notes: {notes}
Prescription: {prescription}"""


def _fallback_pre_visit(reason: str):
    return {
        'failed': True,
        'urgency_level': 'Medium',  # safe default: never silently downgrade urgency on failure
        'chief_complaint': 'Automatic summary unavailable — please review symptoms below manually.',
        'suggested_questions': [],
        'raw': None,
        'error': reason,
    }


def generate_pre_visit_summary(symptoms: str) -> dict:
    """
    Returns dict: {failed, urgency_level, chief_complaint, suggested_questions, raw, error}

    Design choice: on any failure, urgency_level defaults to "Medium" rather
    than "Low". A failed AI call should never cause a genuinely urgent case
    to look calm on the doctor's dashboard — better to slightly over-flag
    than under-flag when the summary can't be trusted.
    """
    client = _get_client()
    if client is None:
        return _fallback_pre_visit('ANTHROPIC_API_KEY not configured')

    try:
        response = client.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=400,
            timeout=15.0,
            messages=[{"role": "user", "content": PRE_VISIT_PROMPT.format(symptoms=symptoms)}],
        )
        raw_text = response.content[0].text.strip()
        # Defensive: strip accidental markdown fences even though we asked it not to.
        if raw_text.startswith('```'):
            raw_text = raw_text.strip('`').lstrip('json').strip()

        data = json.loads(raw_text)

        urgency = data.get('urgency_level', 'Medium')
        if urgency not in ('Low', 'Medium', 'High'):
            urgency = 'Medium'

        questions = data.get('suggested_questions', [])
        if not isinstance(questions, list):
            questions = []

        return {
            'failed': False,
            'urgency_level': urgency,
            'chief_complaint': str(data.get('chief_complaint', ''))[:255],
            'suggested_questions': questions[:3],
            'raw': data,
            'error': None,
        }

    except APITimeoutError as e:
        logger.warning("Pre-visit LLM call timed out: %s", e)
        return _fallback_pre_visit('timeout')
    except APIError as e:
        logger.warning("Pre-visit LLM API error: %s", e)
        return _fallback_pre_visit(f'api_error: {e}')
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError) as e:
        logger.warning("Pre-visit LLM returned unparseable output: %s", e)
        return _fallback_pre_visit('unparseable_response')
    except Exception as e:  # last-resort net: an LLM failure must NEVER break booking
        logger.exception("Unexpected error generating pre-visit summary")
        return _fallback_pre_visit(f'unexpected: {e}')


def generate_post_visit_summary(clinical_notes: str, prescription: str) -> dict:
    """Returns dict: {failed, summary, error}"""
    fallback_summary = (
        "A plain-language summary could not be generated automatically. "
        "Please refer to the doctor's notes and prescription below, "
        "or contact the clinic with any questions."
    )

    client = _get_client()
    if client is None:
        return {'failed': True, 'summary': fallback_summary, 'error': 'ANTHROPIC_API_KEY not configured'}

    try:
        response = client.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=500,
            timeout=15.0,
            messages=[{
                "role": "user",
                "content": POST_VISIT_PROMPT.format(notes=clinical_notes, prescription=prescription),
            }],
        )
        summary = response.content[0].text.strip()
        return {'failed': False, 'summary': summary, 'error': None}

    except APITimeoutError as e:
        logger.warning("Post-visit LLM call timed out: %s", e)
        return {'failed': True, 'summary': fallback_summary, 'error': 'timeout'}
    except APIError as e:
        logger.warning("Post-visit LLM API error: %s", e)
        return {'failed': True, 'summary': fallback_summary, 'error': f'api_error: {e}'}
    except Exception as e:
        logger.exception("Unexpected error generating post-visit summary")
        return {'failed': True, 'summary': fallback_summary, 'error': f'unexpected: {e}'}
