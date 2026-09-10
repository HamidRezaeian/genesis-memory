"""GENESIS Structural Tool-Pair Compactor & Integrity Validator.

Guarantees:
1. Tool-Pair Preservation: Never orphans tool_call / tool_result blocks.
2. Provider Schemas: Strict validation for OpenAI and Anthropic message shapes.
3. Locked Pipeline: Strip history -> Pin Memory Capsule -> Assemble.
4. Idempotence: compact(compact(m)) == compact(m).
5. Token Metrics: Accurately estimates stripped and retained tokens.
"""

import copy
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("genesis.proxy.compactor")

CAPSULE_TAG_START = "<!-- GENESIS_PINNED_CAPSULE_START -->"
CAPSULE_TAG_END = "<!-- GENESIS_PINNED_CAPSULE_END -->"

# Instruction-bearing roles are pinned as an ordered prefix, never pruned as
# history (R1: a leading `developer` message carries app constraints that must
# survive compaction byte-identical, in original relative order).
INSTRUCTION_ROLES = ("system", "developer")


class ToolIntegrityError(ValueError):
    """Raised when message sequence violates provider tool-call integrity."""
    pass


def estimate_tokens(text_or_obj: Any) -> int:
    """Fast deterministic token estimation (~4 chars per token)."""
    if isinstance(text_or_obj, str):
        return max(1, len(text_or_obj) // 4)
    try:
        s = json.dumps(text_or_obj)
        return max(1, len(s) // 4)
    except Exception:
        return 1


def validate_openai_tool_integrity(messages: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    """Strictly validates OpenAI tool call/response adjacency and pairings."""
    pending_tool_calls: set[str] = set()
    active_call_order: list[str] = []

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        tool_calls = msg.get("tool_calls")

        # 1. Assistant message with tool calls
        if role == "assistant" and tool_calls:
            for tc in tool_calls:
                call_id = tc.get("id")
                if not call_id:
                    return False, f"Message {idx}: assistant tool_call missing 'id'"
                pending_tool_calls.add(call_id)
                active_call_order.append(call_id)

        # 2. Tool response message
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if not tool_call_id:
                return False, f"Message {idx}: tool role message missing 'tool_call_id'"
            if tool_call_id not in pending_tool_calls:
                return False, f"Message {idx}: orphaned tool message references unknown or already-cleared id '{tool_call_id}'"
            pending_tool_calls.remove(tool_call_id)

        # 3. User message arriving while tool calls are still pending
        elif role == "user" and pending_tool_calls:
            return False, f"Message {idx}: user message arrived while tool calls {pending_tool_calls} are still unresolved"

    if pending_tool_calls:
        return False, f"Unresolved tool_calls at end of sequence: {pending_tool_calls}"

    return True, None


def validate_anthropic_tool_integrity(messages: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    """Strictly validates Anthropic tool_use / tool_result pairings."""
    pending_tool_uses: set[str] = set()

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                b_type = block.get("type")
                if role == "assistant" and b_type == "tool_use":
                    tool_id = block.get("id")
                    if not tool_id:
                        return False, f"Message {idx}: tool_use block missing 'id'"
                    pending_tool_uses.add(tool_id)
                elif role == "user" and b_type == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if not tool_use_id:
                        return False, f"Message {idx}: tool_result block missing 'tool_use_id'"
                    if tool_use_id not in pending_tool_uses:
                        return False, f"Message {idx}: orphaned tool_result for unknown id '{tool_use_id}'"
                    pending_tool_uses.remove(tool_use_id)

    if pending_tool_uses:
        return False, f"Unresolved tool_uses at end of sequence: {pending_tool_uses}"

    return True, None


def validate_tool_integrity(messages: List[Dict[str, Any]], format_type: str = "openai") -> Tuple[bool, Optional[str]]:
    """Dispatches validation per provider format."""
    if format_type.lower() == "anthropic":
        return validate_anthropic_tool_integrity(messages)
    return validate_openai_tool_integrity(messages)


def _group_openai_turns(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:
    """Groups OpenAI messages into (pinned_instruction_messages, list_of_turns).
    
    A turn begins with a 'user' message and includes all subsequent assistant/tool messages
    until the next 'user' message. Leading system/developer messages are pinned.
    """
    system_messages: List[Dict[str, Any]] = []
    turns: List[List[Dict[str, Any]]] = []
    current_turn: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        if role in INSTRUCTION_ROLES:
            # If instruction message appears before any turns, treat as pinned
            if not turns and not current_turn:
                system_messages.append(msg)
            else:
                # Instruction message embedded mid-conversation, treat as part of current turn
                if not current_turn:
                    current_turn = [msg]
                else:
                    current_turn.append(msg)
        elif role == "user":
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        else:
            # assistant or tool
            if not current_turn:
                # Lead assistant message (rare, e.g. prefill)
                current_turn = [msg]
            else:
                current_turn.append(msg)

    if current_turn:
        turns.append(current_turn)

    return system_messages, turns


def _group_anthropic_turns(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:
    """Groups Anthropic messages into (instruction_messages, list_of_turns)."""
    system_messages: List[Dict[str, Any]] = []
    turns: List[List[Dict[str, Any]]] = []
    current_turn: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role in INSTRUCTION_ROLES:
            if not turns and not current_turn:
                system_messages.append(msg)
            else:
                if not current_turn:
                    current_turn = [msg]
                else:
                    current_turn.append(msg)
            continue

        # Check if user message is pure tool_result (continuation of previous turn's tool loop)
        is_pure_tool_result = False
        if role == "user" and isinstance(content, list):
            is_pure_tool_result = all(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )

        if role == "user" and not is_pure_tool_result:
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        else:
            if not current_turn:
                current_turn = [msg]
            else:
                current_turn.append(msg)

    if current_turn:
        turns.append(current_turn)

    return system_messages, turns


def compact_messages(
    messages: List[Dict[str, Any]],
    max_history_turns: int = 1,
    memory_capsule: Optional[str] = None,
    format_type: str = "openai",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Compacts conversation history while preserving atomic tool pairs and memory pins.
    
    Returns:
        (compacted_messages, metadata_dict)
    """
    if not messages:
        return [], {"tokens_stripped": 0, "tokens_retained": 0, "turns_compacted": 0}

    # Verify input integrity first
    is_valid, err = validate_tool_integrity(messages, format_type)
    if not is_valid:
        raise ToolIntegrityError(f"Input messages violate tool integrity: {err}")

    input_tokens = sum(estimate_tokens(m) for m in messages)

    # 1. Group messages into system and turns
    if format_type.lower() == "anthropic":
        system_msgs, turns = _group_anthropic_turns(messages)
    else:
        system_msgs, turns = _group_openai_turns(messages)

    # 2. History pruning (Retain active working set + max_history_turns for TLB)
    # Active working set is the last turn (turns[-1])
    turns_compacted = 0
    retained_turns: List[List[Dict[str, Any]]] = []

    if turns:
        # Keep up to (max_history_turns + 1) turns (active turn + K historical turns)
        num_to_keep = max(1, max_history_turns + 1)
        retained_turns = turns[-num_to_keep:]
        turns_compacted = max(0, len(turns) - num_to_keep)

    # Flatten retained messages
    compacted: List[Dict[str, Any]] = []
    for s_msg in system_msgs:
        compacted.append(copy.deepcopy(s_msg))
    for turn in retained_turns:
        for msg in turn:
            compacted.append(copy.deepcopy(msg))

    # 2b. Atomic pair repair (auditor fix 2026-09-06): turn-count pruning may sever a
    # tool_use from its tool_result (e.g. a mixed result+text user message starts a new
    # turn while its use stays behind). Pairs are INDIVISIBLE: pull the orphaned use
    # forward, stripped to the needed blocks only (a full rescued message could dangle
    # its other uses, which the validator also rejects). Turn bounds are soft.
    compacted, pairs_repaired = _repair_orphaned_pairs(compacted, messages, format_type)

    # 3. Memory Capsule Pinning (Order of operations: Strip -> Pin -> Inject)
    # Check if a capsule is already pinned in system messages (for idempotence)
    existing_capsule_idx = -1
    for idx, msg in enumerate(compacted):
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str) and CAPSULE_TAG_START in content:
                existing_capsule_idx = idx
                break

    if memory_capsule:
        capsule_formatted = f"{CAPSULE_TAG_START}\n[GENESIS Subconscious Memory]\n{memory_capsule.strip()}\n{CAPSULE_TAG_END}"
        if existing_capsule_idx >= 0:
            # Update existing pinned capsule in place
            old_content = compacted[existing_capsule_idx].get("content", "")
            prefix = old_content.split(CAPSULE_TAG_START)[0].strip()
            suffix = old_content.split(CAPSULE_TAG_END)[1].strip() if CAPSULE_TAG_END in old_content else ""
            new_content = f"{prefix}\n\n{capsule_formatted}\n\n{suffix}".strip()
            compacted[existing_capsule_idx]["content"] = new_content
        else:
            # Inject pinned capsule: find first system message or prepend new system message
            if compacted and compacted[0].get("role") == "system":
                orig = compacted[0].get("content", "")
                compacted[0]["content"] = f"{orig}\n\n{capsule_formatted}".strip()
            else:
                # Insert after any leading instruction run so system/developer
                # relative order is preserved and memory follows instructions.
                idx = 0
                while idx < len(compacted) and compacted[idx].get("role") in INSTRUCTION_ROLES:
                    idx += 1
                compacted.insert(idx, {"role": "system", "content": capsule_formatted})

    # 4. Strict Schema Validation on Output
    valid_out, out_err = validate_tool_integrity(compacted, format_type)
    if not valid_out:
        raise ToolIntegrityError(f"Compactor produced invalid tool structure: {out_err}")

    output_tokens = sum(estimate_tokens(m) for m in compacted)
    tokens_stripped = max(0, input_tokens - output_tokens)

    meta = {
        "tokens_stripped": tokens_stripped,
        "tokens_retained": output_tokens,
        "turns_compacted": turns_compacted,
        "pairs_repaired": pairs_repaired,
        "format": format_type,
    }

    return compacted, meta


def _iter_tool_uses(messages: List[Dict[str, Any]],
                    format_type: str):
    """Yield (message, use_id) for every tool-call site (both providers)."""
    anthropic = format_type.lower() == "anthropic"
    for m in messages:
        if anthropic:
            if m.get("role") == "assistant":
                content = m.get("content")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                            yield m, b["id"]
        else:
            if m.get("role") == "assistant":
                for tc in (m.get("tool_calls") or []):
                    if isinstance(tc, dict) and tc.get("id"):
                        yield m, tc["id"]


def _iter_tool_results(messages: List[Dict[str, Any]],
                       format_type: str):
    """Yield (message, use_id) for every tool-result site (both providers)."""
    anthropic = format_type.lower() == "anthropic"
    for m in messages:
        if anthropic:
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result" \
                                and b.get("tool_use_id"):
                            yield m, b["tool_use_id"]
        else:
            if m.get("role") == "tool" and m.get("tool_call_id"):
                yield m, m["tool_call_id"]


def _slim_use_message(msg: Dict[str, Any], keep_ids, format_type: str) -> Dict[str, Any]:
    """Copy a tool_use message keeping ONLY the needed call blocks.

    A rescued full message could dangle its other (unneeded) uses, which the
    validator rejects symmetric to orphaned results. Minimal rescue only.
    """
    out = copy.deepcopy(msg)
    if format_type.lower() == "anthropic":
        out["content"] = [b for b in out.get("content", [])
                          if isinstance(b, dict) and b.get("type") == "tool_use"
                          and b.get("id") in keep_ids]
    else:
        out["tool_calls"] = [tc for tc in (out.get("tool_calls") or [])
                             if isinstance(tc, dict) and tc.get("id") in keep_ids]
        out.pop("content", None)
    return out


def _repair_orphaned_pairs(compacted: List[Dict[str, Any]],
                           original: List[Dict[str, Any]],
                           format_type: str):
    """Pull pruned-away tool_use messages forward to their orphaned results.

    Pure function, no I/O. Returns (fixed_list, n_repaired). Unknown ids (absent
    from the original input too) are left for the validator to reject properly.
    """
    present = set()
    for _, uid in _iter_tool_uses(compacted, format_type):
        present.add(uid)
    needed: List[str] = []
    for _, rid in _iter_tool_results(compacted, format_type):
        if rid not in present and rid not in needed:
            needed.append(rid)
    if not needed:
        return compacted, 0
    use_by_id = {}
    for m, uid in _iter_tool_uses(original, format_type):
        use_by_id.setdefault(uid, m)
    want: Dict[int, List[str]] = {}
    for i, m in enumerate(compacted):
        for _, rid in _iter_tool_results([m], format_type):
            if rid in needed and rid in use_by_id:
                want.setdefault(i, []).append(rid)
    fixed: List[Dict[str, Any]] = []
    repaired = 0
    for i, m in enumerate(compacted):
        for rid in want.get(i, []):
            fixed.append(_slim_use_message(use_by_id[rid], [rid], format_type))
            repaired += 1
        fixed.append(m)
    return fixed, repaired
