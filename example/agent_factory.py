#!/usr/bin/env python3
"""File-first agent factory for TaiLOR.

This tool provides a Python-exclusive workflow for creating, configuring,
and reporting on agents without requiring .NET components.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

DEFAULT_LAYOUT = {
    "configs": "configs",
    "clients": "clients",
    "conversations": "conversations",
    "runtime": "runtime",
    "reports": "reports",
    "instructions": "instructions",
}


def utc_now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return re.sub(r"-+", "-", text).strip("-") or "agent"


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _resolve_factory_db_path(factory_root: Path, db_path: Path | None) -> Path | None:
    """Resolve optional metrics DB so it does not depend on process cwd (relative paths vs factory parent)."""
    if db_path is None:
        return None
    p = Path(db_path).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (Path(factory_root).resolve().parent / p).resolve()


class AgentFactory:
    def __init__(self, root: Path, db_path: Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.db_path = _resolve_factory_db_path(self.root, db_path)
        self.layout_paths = {name: self.root / rel for name, rel in DEFAULT_LAYOUT.items()}

    def init_layout(self) -> None:
        for path in self.layout_paths.values():
            path.mkdir(parents=True, exist_ok=True)
        readme = self.root / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Agent Factory Store\n\n"
                "This directory is managed by `scripts/agent_factory.py`.\n"
                "You can manually edit JSON files; run `validate` after edits.\n",
                encoding="utf-8",
            )
        instructions = self.instructions_path()
        if not instructions.exists():
            self.save_json(
                instructions,
                {
                    "schema_version": "1.0.0",
                    "updated_utc": utc_now(),
                    "rules": [],
                },
            )

    def agent_config_path(self, agent_key: str) -> Path:
        return self.layout_paths["configs"] / f"{agent_key}.json"

    def runtime_path(self, agent_key: str) -> Path:
        return self.layout_paths["runtime"] / f"{agent_key}.json"

    def conversation_path(self, agent_key: str) -> Path:
        return self.layout_paths["conversations"] / f"{agent_key}.json"

    def clients_path(self, agent_key: str) -> Path:
        return self.layout_paths["clients"] / f"{agent_key}.json"

    def instructions_path(self) -> Path:
        return self.layout_paths["instructions"] / "central_instructions.json"

    def load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def save_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def load_instruction_center(self) -> dict[str, Any]:
        default = {
            "schema_version": "1.0.0",
            "updated_utc": utc_now(),
            "rules": [],
        }
        data = self.load_json(self.instructions_path(), default)
        if not isinstance(data, dict):
            return default
        data.setdefault("schema_version", "1.0.0")
        data.setdefault("updated_utc", utc_now())
        rules = data.get("rules")
        if not isinstance(rules, list):
            data["rules"] = []
        return data

    def save_instruction_center(self, data: dict[str, Any]) -> None:
        data["updated_utc"] = utc_now()
        self.save_json(self.instructions_path(), data)

    def list_agents(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.layout_paths["configs"].glob("*.json")):
            cfg = self.load_json(path, {})
            agent_key = path.stem
            clients = self.load_json(self.clients_path(agent_key), [])
            conversations = self.load_json(self.conversation_path(agent_key), [])
            runtime = self.load_json(self.runtime_path(agent_key), {})
            rows.append(
                {
                    "agent_key": agent_key,
                    "agent_id": cfg.get("agent_id"),
                    "name": cfg.get("display_name"),
                    "is_active": cfg.get("is_active", True),
                    "clients": len(clients),
                    "conversations": len(conversations),
                    "current_action": runtime.get("current_action") or cfg.get("activity", {}).get("current_action"),
                }
            )
        return rows

    def create_agent(
        self,
        display_name: str,
        personality: str,
        writing_style: str,
        backstory: str,
        demographic_summary: str,
        instructions: list[str],
        contact_methods: list[dict[str, Any]],
        tags: list[str],
    ) -> dict[str, Any]:
        self.init_layout()
        agent_key = slugify(display_name)
        if self.agent_config_path(agent_key).exists():
            agent_key = f"{agent_key}-{new_id()[:8]}"

        config = {
            "schema_version": "1.0.0",
            "agent_id": new_id(),
            "agent_key": agent_key,
            "display_name": display_name,
            "is_active": True,
            "created_utc": utc_now(),
            "updated_utc": utc_now(),
            "tags": tags,
            "demographics": {
                "summary": demographic_summary,
            },
            "persona": {
                "personality": personality,
                "writing_style": writing_style,
                "backstory": backstory,
                "special_client_instructions": instructions,
            },
            "contact_methods": contact_methods,
            "activity": {
                "current_action": "idle",
                "current_action_utc": utc_now(),
                "next_action": "none",
                "next_action_utc": None,
                "details": "Agent created by agent factory.",
            },
        }

        self.save_json(self.agent_config_path(agent_key), config)
        self.save_json(self.clients_path(agent_key), [])
        self.save_json(self.conversation_path(agent_key), [])
        self.save_json(
            self.runtime_path(agent_key),
            {
                "updated_utc": utc_now(),
                "current_action": "idle",
                "details": "No active task.",
                "last_completed_action": None,
                "next_action": None,
            },
        )
        return config

    def validate_configs(self) -> list[str]:
        errors: list[str] = []
        required_top = ["schema_version", "agent_id", "agent_key", "display_name", "persona", "contact_methods", "demographics"]
        required_persona = ["personality", "writing_style", "backstory", "special_client_instructions"]
        required_contact = ["id", "type", "value", "is_active"]

        for path in sorted(self.layout_paths["configs"].glob("*.json")):
            cfg = self.load_json(path, {})
            missing = [k for k in required_top if k not in cfg]
            if missing:
                errors.append(f"{path}: missing top-level keys: {', '.join(missing)}")
                continue
            persona = cfg.get("persona")
            if not isinstance(persona, dict):
                errors.append(f"{path}: persona must be object")
                continue
            missing_persona = [k for k in required_persona if k not in persona]
            if missing_persona:
                errors.append(f"{path}: missing persona keys: {', '.join(missing_persona)}")
            contact_methods = cfg.get("contact_methods")
            if not isinstance(contact_methods, list) or not contact_methods:
                errors.append(f"{path}: contact_methods must be non-empty list")
                continue
            for idx, contact in enumerate(contact_methods):
                if not isinstance(contact, dict):
                    errors.append(f"{path}: contact_methods[{idx}] must be object")
                    continue
                missing_contact = [k for k in required_contact if k not in contact]
                if missing_contact:
                    errors.append(
                        f"{path}: contact_methods[{idx}] missing keys: {', '.join(missing_contact)}"
                    )
        return errors

    def add_instruction_rule(
        self,
        instruction: str,
        label: str,
        applies_to_all: bool,
        agent_keys: list[str],
        tags: list[str],
        client_ids: list[str],
        priority: int,
    ) -> dict[str, Any]:
        center = self.load_instruction_center()
        rule = {
            "rule_id": new_id(),
            "label": label or f"rule-{len(center['rules']) + 1}",
            "instruction": instruction.strip(),
            "priority": int(priority),
            "is_active": True,
            "created_utc": utc_now(),
            "targets": {
                "all_agents": bool(applies_to_all),
                "agent_keys": dedupe_keep_order(agent_keys),
                "tags": dedupe_keep_order(tags),
                "client_ids": dedupe_keep_order(client_ids),
            },
        }
        center["rules"].append(rule)
        center["rules"] = sorted(center["rules"], key=lambda item: (int(item.get("priority", 0)), str(item.get("label", ""))))
        self.save_instruction_center(center)
        return rule

    def _rule_applies_to_agent(self, rule: dict[str, Any], agent_cfg: dict[str, Any], agent_key: str) -> bool:
        if not bool(rule.get("is_active", True)):
            return False
        targets = rule.get("targets", {})
        if not isinstance(targets, dict):
            targets = {}
        if bool(targets.get("all_agents", False)):
            return True
        target_agent_keys = {str(item).strip() for item in targets.get("agent_keys", []) if str(item).strip()}
        if agent_key in target_agent_keys:
            return True
        target_tags = {str(item).strip() for item in targets.get("tags", []) if str(item).strip()}
        cfg_tags = {str(item).strip() for item in agent_cfg.get("tags", []) if str(item).strip()}
        return bool(target_tags.intersection(cfg_tags))

    def _rule_applies_to_client(self, rule: dict[str, Any], client: dict[str, Any], agent_cfg: dict[str, Any], agent_key: str) -> bool:
        if not bool(rule.get("is_active", True)):
            return False
        targets = rule.get("targets", {})
        if not isinstance(targets, dict):
            return False
        client_ids = {str(item).strip() for item in targets.get("client_ids", []) if str(item).strip()}
        client_id = str(client.get("client_id") or "").strip()
        if not client_ids or not client_id or client_id not in client_ids:
            return False
        has_agent_scope = bool(targets.get("all_agents")) or bool(targets.get("agent_keys")) or bool(targets.get("tags"))
        if not has_agent_scope:
            return True
        return self._rule_applies_to_agent(rule, agent_cfg, agent_key)

    def apply_central_instructions(self, dry_run: bool = False, agent_filter: list[str] | None = None) -> dict[str, Any]:
        center = self.load_instruction_center()
        rules = sorted(
            [rule for rule in center.get("rules", []) if isinstance(rule, dict)],
            key=lambda item: (int(item.get("priority", 0)), str(item.get("label", ""))),
        )
        selected = {item.strip() for item in (agent_filter or []) if item.strip()}
        updates: list[dict[str, Any]] = []

        for path in sorted(self.layout_paths["configs"].glob("*.json")):
            agent_key = path.stem
            if selected and agent_key not in selected:
                continue
            cfg = self.load_json(path, {})
            if not isinstance(cfg, dict):
                continue
            persona = cfg.get("persona")
            if not isinstance(persona, dict):
                continue
            clients = self.load_json(self.clients_path(agent_key), [])
            if not isinstance(clients, list):
                clients = []

            local_instructions = persona.get("local_instructions")
            if not isinstance(local_instructions, list):
                local_instructions = list(persona.get("special_client_instructions", []))
            local_instructions = dedupe_keep_order([str(item) for item in local_instructions])

            agent_rules = [
                rule
                for rule in rules
                if not isinstance(rule.get("targets"), dict)
                or not [item for item in rule.get("targets", {}).get("client_ids", []) if str(item).strip()]
            ]
            managed_instructions = dedupe_keep_order(
                [
                    str(rule.get("instruction", ""))
                    for rule in agent_rules
                    if self._rule_applies_to_agent(rule, cfg, agent_key)
                ]
            )
            effective_instructions = dedupe_keep_order(local_instructions + managed_instructions)
            applied_rule_labels = [
                str(rule.get("label", ""))
                for rule in agent_rules
                if self._rule_applies_to_agent(rule, cfg, agent_key)
            ]
            client_updates = 0
            for client in clients:
                if not isinstance(client, dict):
                    continue
                client_local = client.get("local_instructions")
                if not isinstance(client_local, list):
                    client_local = list(client.get("special_instructions", []))
                client_local = dedupe_keep_order([str(item) for item in client_local])
                client_managed = dedupe_keep_order(
                    [
                        str(rule.get("instruction", ""))
                        for rule in rules
                        if self._rule_applies_to_client(rule, client, cfg, agent_key)
                    ]
                )
                client_effective = dedupe_keep_order(client_local + client_managed)
                client["local_instructions"] = client_local
                client["managed_instructions"] = client_managed
                client["special_instructions"] = client_effective
                client_updates += 1

            updates.append(
                {
                    "agent_key": agent_key,
                    "local_instructions": len(local_instructions),
                    "managed_instructions": len(managed_instructions),
                    "effective_instructions": len(effective_instructions),
                    "applied_rules": applied_rule_labels,
                    "updated_clients": client_updates,
                }
            )

            if dry_run:
                continue

            persona["local_instructions"] = local_instructions
            persona["managed_instructions"] = managed_instructions
            persona["special_client_instructions"] = effective_instructions
            cfg["updated_utc"] = utc_now()
            self.save_json(path, cfg)
            self.save_json(self.clients_path(agent_key), clients)

        return {
            "dry_run": dry_run,
            "selected_agents": sorted(selected) if selected else "all",
            "updated_agents": len(updates),
            "agents": updates,
        }

    def _db_metrics_for_agent(self, agent_config: dict[str, Any]) -> dict[str, Any]:
        if not self.db_path or not self.db_path.exists():
            return {}
        persona_id = str(agent_config.get("persona_id") or "")
        agent_id = str(agent_config.get("agent_id") or "")
        if not agent_id and not persona_id:
            return {}

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            convo_count = 0
            client_count = 0
            if agent_id:
                cur.execute("SELECT COUNT(*) AS total FROM Conversation WHERE AgentId = ?", (agent_id,))
                convo_count = int(cur.fetchone()["total"])
                cur.execute(
                    "SELECT COUNT(DISTINCT ClientId) AS total FROM Conversation WHERE AgentId = ?",
                    (agent_id,),
                )
                client_count = int(cur.fetchone()["total"])
            elif persona_id:
                cur.execute("SELECT COUNT(*) AS total FROM Conversation WHERE PersonaId = ?", (persona_id,))
                convo_count = int(cur.fetchone()["total"])
                cur.execute(
                    "SELECT COUNT(DISTINCT ClientId) AS total FROM Conversation WHERE PersonaId = ?",
                    (persona_id,),
                )
                client_count = int(cur.fetchone()["total"])

            return {
                "db_clients": client_count,
                "db_conversations": convo_count,
            }

    def report_agent(self, agent_key: str) -> dict[str, Any]:
        cfg = self.load_json(self.agent_config_path(agent_key), {})
        if not cfg:
            raise FileNotFoundError(f"Agent config not found: {agent_key}")
        clients = self.load_json(self.clients_path(agent_key), [])
        conversations = self.load_json(self.conversation_path(agent_key), [])
        runtime = self.load_json(self.runtime_path(agent_key), {})
        db_metrics = self._db_metrics_for_agent(cfg)

        return {
            "agent_key": agent_key,
            "name": cfg.get("display_name"),
            "is_active": cfg.get("is_active", True),
            "configuration": cfg,
            "counts": {
                "file_clients": len(clients),
                "file_conversations": len(conversations),
                **db_metrics,
            },
            "runtime": runtime,
            "conversation_summaries": [
                {
                    "id": c.get("conversation_id"),
                    "client_id": c.get("client_id"),
                    "status": c.get("status"),
                    "last_activity_utc": c.get("last_activity_utc"),
                    "current_goal": c.get("current_goal"),
                }
                for c in conversations
            ],
            "client_summaries": [
                {
                    "id": c.get("client_id"),
                    "name": c.get("name"),
                    "status": c.get("status"),
                    "last_contact_utc": c.get("last_contact_utc"),
                }
                for c in clients
            ],
        }


def parse_contact_method(value: str) -> dict[str, Any]:
    parts = [p.strip() for p in value.split("|")]
    if len(parts) < 2:
        raise ValueError("Contact methods must be TYPE|VALUE or TYPE|VALUE|LABEL")
    method_type = parts[0]
    method_value = parts[1]
    label = parts[2] if len(parts) >= 3 else method_type
    return {
        "id": new_id(),
        "type": method_type,
        "value": method_value,
        "label": label,
        "provider": "",
        "metadata": {},
        "is_primary": False,
        "is_active": True,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--factory-root", default="agent_factory", help="Factory root directory")
    parser.add_argument(
        "--db-path",
        default="tailor-state.db",
        help="Optional sqlite for metrics; relative paths resolve next to the parent of --factory-root (repo root when factory is agent_factory/)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TaiLOR Python Agent Factory")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize file-based agent factory layout")
    add_common_args(init)

    create = sub.add_parser("create-agent", help="Create a new agent config and sidecar files")
    add_common_args(create)
    create.add_argument("--name", required=True)
    create.add_argument("--personality", required=True)
    create.add_argument("--writing-style", required=True)
    create.add_argument("--backstory", required=True)
    create.add_argument("--demographics", required=True)
    create.add_argument("--instruction", action="append", default=[])
    create.add_argument("--contact", action="append", default=[], help="TYPE|VALUE|LABEL (LABEL optional)")
    create.add_argument("--tag", action="append", default=[])

    lst = sub.add_parser("list-agents", help="List all file-based agents and current counts")
    add_common_args(lst)

    show = sub.add_parser("show-agent", help="Print a full report for one agent")
    add_common_args(show)
    show.add_argument("--agent-key", required=True)
    show.add_argument("--output-json")

    report = sub.add_parser("report", help="Generate report for all agents")
    add_common_args(report)
    report.add_argument("--output-json", default="agent_factory/reports/agent_report.json")

    validate = sub.add_parser("validate", help="Validate agent config files")
    add_common_args(validate)

    instruction_add = sub.add_parser("instruction-add", help="Add a centralized instruction rule")
    add_common_args(instruction_add)
    instruction_add.add_argument("--instruction", required=True, help="Instruction text to enforce")
    instruction_add.add_argument("--label", default="", help="Human-readable label for the rule")
    instruction_add.add_argument("--all-agents", action="store_true", help="Apply this rule to all agents")
    instruction_add.add_argument("--agent-key", action="append", default=[], help="Agent key target (repeatable)")
    instruction_add.add_argument("--tag", action="append", default=[], help="Tag target (repeatable)")
    instruction_add.add_argument("--client-id", action="append", default=[], help="Client id target (repeatable)")
    instruction_add.add_argument("--priority", type=int, default=100, help="Lower values apply first")

    instruction_apply = sub.add_parser("instruction-apply", help="Apply centralized instruction rules to agent configs")
    add_common_args(instruction_apply)
    instruction_apply.add_argument("--agent-key", action="append", default=[], help="Optional agent key filter")
    instruction_apply.add_argument("--dry-run", action="store_true", help="Preview changes without writing")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    factory = AgentFactory(root=Path(args.factory_root), db_path=Path(args.db_path) if args.db_path else None)

    if args.command == "init":
        factory.init_layout()
        print(f"Initialized agent factory at {factory.root}")
        return 0

    if args.command == "create-agent":
        contacts = [parse_contact_method(item) for item in args.contact]
        if not contacts:
            raise ValueError("At least one --contact entry is required")
        cfg = factory.create_agent(
            display_name=args.name,
            personality=args.personality,
            writing_style=args.writing_style,
            backstory=args.backstory,
            demographic_summary=args.demographics,
            instructions=args.instruction,
            contact_methods=contacts,
            tags=args.tag,
        )
        print(json.dumps({"created": cfg["agent_key"], "config_path": str(factory.agent_config_path(cfg["agent_key"]))}, indent=2))
        return 0

    if args.command == "list-agents":
        rows = factory.list_agents()
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if args.command == "show-agent":
        report = factory.report_agent(args.agent_key)
        if args.output_json:
            write_report(Path(args.output_json), report)
            print(f"Wrote report to {args.output_json}")
        else:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.command == "report":
        all_rows = []
        for row in factory.list_agents():
            all_rows.append(factory.report_agent(row["agent_key"]))
        payload = {
            "generated_utc": utc_now(),
            "agent_count": len(all_rows),
            "agents": all_rows,
        }
        write_report(Path(args.output_json), payload)
        print(f"Wrote report to {args.output_json}")
        return 0

    if args.command == "validate":
        errors = factory.validate_configs()
        if errors:
            print("Validation errors:")
            for e in errors:
                print(f" - {e}")
            return 1
        print("All agent configs validated successfully.")
        return 0

    if args.command == "instruction-add":
        if not args.all_agents and not args.agent_key and not args.tag and not args.client_id:
            raise ValueError("Provide at least one target: --all-agents, --agent-key, --tag, or --client-id")
        rule = factory.add_instruction_rule(
            instruction=args.instruction,
            label=args.label,
            applies_to_all=args.all_agents,
            agent_keys=args.agent_key,
            tags=args.tag,
            client_ids=args.client_id,
            priority=args.priority,
        )
        print(json.dumps({"added_rule": rule}, indent=2, ensure_ascii=False))
        return 0

    if args.command == "instruction-apply":
        result = factory.apply_central_instructions(dry_run=args.dry_run, agent_filter=args.agent_key)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
