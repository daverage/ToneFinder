#!/usr/bin/env python3
"""AI GP-50 Tone Builder local Flask entry point."""

from __future__ import annotations

import re

import requests
from flask import jsonify, request, send_file

import tone_finder
from gp50.preset import PresetError, create_preset
from gp50.rig_builder import build_rig
from gp50.validator import RigValidationError, validate_rig

app = tone_finder.app


def preset_filename(name: str) -> str:
    """Return a portable download filename with an ASCII alphanumeric stem."""
    stem = re.sub(r"[^A-Za-z0-9]+", "", str(name or ""))
    return f"{stem or 'gp50preset'}.prst"


@app.post("/api/build-rig")
def api_build_rig():
    try:
        body = request.get_json(force=True) or {}
        if not str(body.get("query", "")).strip():
            return jsonify(error="A tone request is required."), 400
        # Share the same lock /api/search uses, so a rig build can't overlap
        # with a tone/amp search either — see LLM_BUSY_LOCK's comment.
        with tone_finder.LLM_BUSY_LOCK:
            rig = build_rig(body, tone_finder.lm_json)
        rig["snaptone_status"] = "This preset uses the GP-50's built-in amp and cab."
        return jsonify(rig=rig)
    except RigValidationError as exc:
        return jsonify(error="The AI returned an invalid GP-50 plan.", validation_errors=exc.errors), 422
    except requests.HTTPError as exc:
        detail = getattr(exc.response, "text", "")[:800]
        return jsonify(error=f"Local LLM request failed ({tone_finder.LMSTUDIO_BASE}): {exc}\n{detail}"), 502
    except requests.Timeout:
        # Building a full rig sends the whole relevant GP-50 catalogue in the
        # prompt, so it's far heavier than a tone-interpretation call and can
        # easily outrun LM_JSON_TIMEOUT (default 180s) on a local model even
        # though search requests complete fine. Surface that distinction
        # instead of a raw "Read timed out" message.
        return jsonify(
            error=(
                f"The local LLM at {tone_finder.LMSTUDIO_BASE} did not respond within "
                f"LM_JSON_TIMEOUT ({tone_finder.LM_JSON_TIMEOUT}s). Building a full GP-50 "
                "preset sends a much larger prompt than a tone search, so it can need more "
                "time on local hardware. Raise LM_JSON_TIMEOUT in .env and restart, or use a "
                "smaller/faster model."
            )
        ), 504
    except Exception as exc:
        return jsonify(error=str(exc)), 502


@app.post("/api/create-preset")
def api_create_preset():
    try:
        body = request.get_json(force=True) or {}
        # Validate before serialization to make client-side edits untrusted by design.
        rig = validate_rig(body)
        data = create_preset(rig)
        from io import BytesIO
        return send_file(
            BytesIO(data), mimetype="application/octet-stream", as_attachment=True,
            download_name=preset_filename(rig["preset_name"]),
        )
    except (RigValidationError, PresetError) as exc:
        return jsonify(error=str(exc), validation_errors=getattr(exc, "errors", [])), 422
    except Exception as exc:
        return jsonify(error=f"Preset serialization failed: {exc}"), 500


if __name__ == "__main__":
    tone_finder.autostart_llm_if_configured()
    # autostart's own prints only cover the one path where it actually
    # launches a fresh mlx_lm.server; this covers every path (autostart,
    # reusing an already-running server, or LMSTUDIO_BASE pointed at LM
    # Studio directly) with one reliable line — best-effort, since the
    # server may still not be reachable at all (nothing configured yet).
    try:
        print(f"Local LLM: {tone_finder.lm_model()} ({tone_finder.LMSTUDIO_BASE})")
    except Exception as exc:
        print(f"Local LLM: not reachable at {tone_finder.LMSTUDIO_BASE} yet ({exc})")
    print(f"AI GP-50 Tone Builder — http://{tone_finder.HOST}:{tone_finder.PORT}")
    app.run(
        host=tone_finder.HOST, port=tone_finder.PORT, debug=False,
        # Explicit, not just relying on the default: a local LLM can hard-freeze
        # the machine under concurrent load (see LLM_BUSY_LOCK), so this dev
        # server must stay single-threaded even if Flask's default ever changes.
        threaded=False,
    )
