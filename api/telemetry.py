"""api/telemetry.py – Application Insights tracing context manager and Azure OpenAI client factory.

Extracted from api/main.py so that worker.py and chat.py can import
_ai_span / _openai_client without creating a circular dependency with main.py.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time

from api.config import APPINSIGHTS_CONN, OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_API_KEY, OPENAI_MODEL, _get_credential

logger = logging.getLogger("mcp_factory.api")

# ── App Insights telemetry ─────────────────────────────────────────────────
_AI_TRACER = None
if APPINSIGHTS_CONN:
    try:
        from opencensus.ext.azure.log_exporter import AzureLogHandler
        from opencensus.ext.azure.trace_exporter import AzureExporter
        from opencensus.trace.tracer import Tracer
        from opencensus.trace.samplers import AlwaysOnSampler
        _ai_handler = AzureLogHandler(connection_string=APPINSIGHTS_CONN)
        logging.getLogger().addHandler(_ai_handler)
        _AI_TRACER = Tracer(
            exporter=AzureExporter(connection_string=APPINSIGHTS_CONN),
            sampler=AlwaysOnSampler(),
        )
    except Exception as exc:
        logger.warning("App Insights setup failed (telemetry disabled): %s", exc)


@contextlib.contextmanager
def _ai_span(name: str, **props):
    """Emit a custom App Insights event with duration and optional properties.

    Works via two channels:
    - Structured log entry (AzureLogHandler picks up custom_dimensions)
    - OpenCensus trace span (AzureExporter sends to Application Insights)
    Both are best-effort; failures are silently swallowed.
    """
    t0 = time.perf_counter()
    span = None
    try:
        if _AI_TRACER:
            span = _AI_TRACER.start_span(name=name)
            for k, v in props.items():
                span.add_attribute(k, str(v))
        yield
    finally:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if span and _AI_TRACER:
            # Flush in a daemon thread so a slow/unreachable App Insights
            # endpoint can never block the worker thread.
            try:
                threading.Thread(
                    target=_AI_TRACER.end_span, daemon=True, name="ai-flush"
                ).start()
            except Exception as exc:
                logger.warning("App Insights span flush failed: %s", exc)
        dims = {"event": name, "duration_ms": elapsed_ms, **{k: str(v) for k, v in props.items()}}
        # Fire the custom_dimensions log in a daemon thread — AzureLogHandler
        # flushes synchronously and can block 90s if App Insights is slow.
        def _emit_telemetry(d=dims, n=name, ms=elapsed_ms):
            logger.info(
                "[telemetry] %s completed in %dms",
                n, ms,
                extra={"custom_dimensions": d},
            )
        threading.Thread(target=_emit_telemetry, daemon=True, name="ai-log").start()


_CLIENT_LOCK = threading.Lock()
_cached_client = None
_token_expires_at: float = 0.0  # Unix timestamp


def _openai_client():
    """Return a cached OpenAI client.

    Priority:
      1. Direct OpenAI (OPENAI_API_KEY set) — uses openai.OpenAI, no Azure needed.
      2. Azure OpenAI (AZURE_OPENAI_ENDPOINT set) — refreshes AD token when near expiry.
    """
    global _cached_client, _token_expires_at
    now = time.time()

    # ── Direct OpenAI path ────────────────────────────────────────────
    if OPENAI_API_KEY:
        if _cached_client is None:
            with _CLIENT_LOCK:
                if _cached_client is None:
                    from openai import OpenAI
                    _cached_client = OpenAI(api_key=OPENAI_API_KEY)
                    logger.info("[telemetry] Using direct OpenAI client (model: %s)", OPENAI_MODEL)
        return _cached_client

    # ── Azure OpenAI path ─────────────────────────────────────────────
    if _cached_client is None or now >= _token_expires_at - 300:
        with _CLIENT_LOCK:
            # Re-check inside the lock to avoid double init
            if _cached_client is None or now >= _token_expires_at - 300:
                from openai import AzureOpenAI
                credential = _get_credential()
                tok = credential.get_token("https://cognitiveservices.azure.com/.default")
                _cached_client = AzureOpenAI(
                    azure_endpoint=OPENAI_ENDPOINT,
                    api_version="2024-10-21",
                    azure_ad_token=tok.token,
                )
                _token_expires_at = float(tok.expires_on)
                logger.debug("[telemetry] OpenAI client (re)created, token valid until %s", _token_expires_at)
                # Warm up the endpoint — a token refresh implies idle time long
                # enough for the Azure OpenAI endpoint to go cold too.
                threading.Thread(target=_warmup_openai, daemon=True, name="openai-warmup").start()
    return _cached_client


def _warmup_openai() -> None:
    """Send a minimal completion request to eliminate Azure OpenAI cold-start latency.

    Called in a daemon thread after token refresh and at job completion so the
    endpoint is hot before the user opens the chat panel.
    """
    try:
        client = _openai_client()
        client.chat.completions.create(
            model=OPENAI_DEPLOYMENT,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        logger.debug("[telemetry] OpenAI endpoint warm-up complete")
    except Exception as exc:
        logger.debug("[telemetry] OpenAI warm-up skipped (non-critical): %s", exc)


def _proactive_token_refresh() -> None:
    """Daemon thread: sleep until 10 minutes before token expiry, then pre-refresh.

    Prevents the first chat message after a long gap from paying the 3-8 s Azure
    AD credential round-trip.  On startup _token_expires_at is 0, so the first
    iteration fires immediately — eagerly fetching a token and warming the endpoint
    before any user request arrives.
    """
    while True:
        try:
            sleep_for = max(0.0, _token_expires_at - time.time() - 600)
            time.sleep(sleep_for)
            _openai_client()
            logger.debug("[telemetry] Proactive token refresh complete")
        except Exception as exc:
            logger.warning("[telemetry] Proactive token refresh failed: %s", exc)
            time.sleep(60)  # back off before retrying


# Eagerly fetch the token and warm the endpoint on module import so the very
# first user message never pays the Azure AD credential round-trip cost.
threading.Thread(target=_proactive_token_refresh, daemon=True, name="token-refresh").start()
