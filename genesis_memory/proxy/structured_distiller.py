"""GENESIS Structured Distiller — 0-Cost Deterministic Turn Ingestion.

Guarantees:
1. Zero-Cost / Deterministic: Stdlib only, zero LLM calls for tool/action distillation.
2. Complete Extraction: Extracts files touched, commands executed, and test pass/fail results.
3. Synchronization Barrier: Turn N recall awaits Turn N-1 distillation completion (zero stale reads).
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("genesis.proxy.distiller")

RE_PYTEST = re.compile(r"(=+\s*(\d+)\s+passed.*?=+|FAILED\s+([^\s]+)|(\d+)\s+failed)", re.IGNORECASE)
RE_ERROR_SIG = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:Error|Exception|Fault|Interrupt))\b")
RE_FILE_PATH = re.compile(r"\b([a-zA-Z0-9_\-\/\\]+\.(?:py|json|md|c|cpp|h|ts|js|html|css|txt))\b")


PATH_KEYS = {
    "path", "file", "target", "filename", "targetfile", "filepath", "file_path",
    "target_file", "absolutepath", "absolute_path", "source", "dest", "destination",
}


def _clean_path(p: str) -> Optional[str]:
    """Returns a clean, single-line file path if valid, else None."""
    if not p or not isinstance(p, str):
        return None
    p = p.strip()
    if "\n" in p or "\r" in p or len(p) > 260 or len(p) < 2:
        return None
    if p.startswith("http://") or p.startswith("https://") or " " in p:
        return None
    if "." in p or "/" in p or "\\" in p:
        return p.replace("\\", "/").strip()
    return None


class StructuredDistiller:
    """Extracts factual tool events and manages the Turn synchronization barrier."""

    def __init__(self, store: Optional[Any] = None) -> None:
        self.store = store
        self._pending_distillations = 0
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self.last_distilled_engram: Optional[Dict[str, Any]] = None

    def mark_distillation_in_flight(self) -> None:
        """Signals that an async distillation is queued/in flight."""
        self._pending_distillations += 1
        self._idle_event.clear()

    def extract_turn_facts(
        self,
        messages: List[Dict[str, Any]],
        response_text: str = "",
        response_tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Deterministically extracts tools called, files touched, and test outcomes."""
        touched_files: Set[str] = set()
        executed_commands: List[str] = []
        test_outcomes: List[str] = []
        errors_found: Set[str] = set()
        tools_called: List[str] = []

        all_msgs = list(messages)
        if response_tool_calls:
            all_msgs.append({"role": "assistant", "tool_calls": response_tool_calls})
        if response_text:
            all_msgs.append({"role": "assistant", "content": response_text})

        for msg in all_msgs:
            role = msg.get("role")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")

            # Extract from tool_calls
            if tool_calls and isinstance(tool_calls, list):
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    if fn_name:
                        tools_called.append(fn_name)
                    args_raw = fn.get("arguments", "")
                    if args_raw:
                        try:
                            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                            for k, v in args.items():
                                if isinstance(v, str):
                                    k_lower = k.lower()
                                    if k_lower in PATH_KEYS:
                                        cleaned = _clean_path(v)
                                        if cleaned:
                                            touched_files.add(cleaned)
                                    elif k_lower in ("cmd", "command", "commandline"):
                                        cmd_str = v.strip().replace("\n", " ")[:120]
                                        if cmd_str:
                                            executed_commands.append(cmd_str)
                                    elif "\n" not in v and len(v) <= 260:
                                        m = RE_FILE_PATH.fullmatch(v.strip())
                                        if m:
                                            cleaned = _clean_path(m.group(1))
                                            if cleaned:
                                                touched_files.add(cleaned)
                        except Exception:
                            # Fallback regex extraction on raw arguments
                            for m in RE_FILE_PATH.findall(str(args_raw)):
                                cleaned = _clean_path(m)
                                if cleaned:
                                    touched_files.add(cleaned)

            # Extract from tool responses / text content
            text_str = ""
            if isinstance(content, str):
                text_str = content
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        b_type = b.get("type")
                        if b_type == "tool_use":
                            tools_called.append(b.get("name", ""))
                            inp = b.get("input", {})
                            for k, v in inp.items():
                                if isinstance(v, str):
                                    k_lower = k.lower()
                                    if k_lower in PATH_KEYS:
                                        cleaned = _clean_path(v)
                                        if cleaned:
                                            touched_files.add(cleaned)
                                    elif "\n" not in v and len(v) <= 260:
                                        m = RE_FILE_PATH.fullmatch(v.strip())
                                        if m:
                                            cleaned = _clean_path(m.group(1))
                                            if cleaned:
                                                touched_files.add(cleaned)
                        elif b_type == "tool_result":
                            text_str += " " + str(b.get("content", ""))
                        elif b_type == "text":
                            text_str += " " + str(b.get("text", ""))

            if text_str:
                # Check for test outcomes
                pytest_match = RE_PYTEST.search(text_str)
                if pytest_match:
                    test_outcomes.append(pytest_match.group(0).strip())
                # Check for errors
                for err in RE_ERROR_SIG.findall(text_str):
                    errors_found.add(err)
                # Check for files
                for f in RE_FILE_PATH.findall(text_str):
                    cleaned = _clean_path(f)
                    if cleaned and not cleaned.startswith("http"):
                        touched_files.add(cleaned)

        summary_parts = []
        if tools_called:
            summary_parts.append(f"tools=[{', '.join(tools_called[:4])}]")
        if touched_files:
            clean_sorted = [f for f in sorted(list(touched_files)) if _clean_path(f)]
            if clean_sorted:
                summary_parts.append(f"files=[{', '.join(clean_sorted[:4])}]")
        if test_outcomes:
            summary_parts.append(f"tests={test_outcomes[0]}")
        if errors_found:
            summary_parts.append(f"errors=[{', '.join(sorted(list(errors_found))[:3])}]")

        summary_text = " | ".join(summary_parts) if summary_parts else "Turn completed with no external tool actions."

        return {
            "summary_text": summary_text,
            "tools_called": tools_called,
            "touched_files": sorted(list(touched_files)),
            "test_outcomes": test_outcomes,
            "errors_found": sorted(list(errors_found)),
            "commands": executed_commands,
        }

    async def distill_and_ingest(
        self,
        messages: List[Dict[str, Any]],
        response_text: str = "",
        response_tool_calls: Optional[List[Dict[str, Any]]] = None,
        project: str = "genesis",
    ) -> Dict[str, Any]:
        """Ingests turn facts into memory with barrier locking."""
        try:
            facts = self.extract_turn_facts(messages, response_text, response_tool_calls)
            summary = facts["summary_text"]

            engram = {
                "kind": "outcome" if facts["test_outcomes"] or facts["tools_called"] else "fact",
                "project": project,
                "text": f"Agentic Turn Action: {summary}",
                "utility": 1.5 if facts["test_outcomes"] or facts["errors_found"] else 1.0,
                "ts": time.time(),
            }

            if self.store:
                try:
                    engram_id = self.store.remember(
                        text=engram["text"],
                        kind=engram["kind"],
                        project=engram["project"],
                        utility=engram["utility"],
                    )
                    engram["id"] = engram_id
                except Exception as exc:
                    logger.warning("Failed to store distilled engram in Store: %s", exc)

            self.last_distilled_engram = engram
            return engram
        finally:
            self._pending_distillations = max(0, self._pending_distillations - 1)
            if self._pending_distillations == 0:
                self._idle_event.set()

    async def wait_for_pending_distillations(self, timeout: Optional[float] = None) -> bool:
        """Synchronization Barrier: ensures any in-flight distillation finishes before next recall.
        
        Args:
            timeout: Maximum seconds to wait before falling back to deployed state.
            
        Returns:
            True if distillations finished cleanly, False if timed out.
        """
        if timeout is not None and timeout > 0:
            try:
                await asyncio.wait_for(self._idle_event.wait(), timeout=timeout)
                return True
            except asyncio.TimeoutError:
                logger.warning("Barrier wait timed out after %.3fs; falling back to deployed state", timeout)
                return False
        await self._idle_event.wait()
        return True
