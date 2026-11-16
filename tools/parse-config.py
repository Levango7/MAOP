#!/usr/bin/env python
"""MAOP unified config parser — reads agents.yaml (and optionally rules.yaml),
outputs structured JSON to stdout for PowerShell consumption.

Usage:
  python tools/parse-config.py --section agents     # all agents
  python tools/parse-config.py --section routing    # routing table
  python tools/parse-config.py --section loops      # loop config
  python tools/parse-config.py --section workflows  # workflow definitions
  python tools/parse-config.py --section rules      # rules.yaml (optional)
  python tools/parse-config.py --section all        # everything
  python tools/parse-config.py --agent claude       # single agent config
  python tools/parse-config.py --routing-key codegen # routing entry for key

Replaces three hand-rolled regex YAML parsers in maop-plan.ps1, maop-loop.ps1, delegate-plugin.ps1.
"""

import argparse
import json
import os
import sys

import yaml

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
AGENTS_FILE = os.path.join(CONFIG_DIR, "agents.yaml")
RULES_FILE = os.path.join(CONFIG_DIR, "rules.yaml")


def load_agents():
    if not os.path.exists(AGENTS_FILE):
        return {}
    with open(AGENTS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_rules():
    if not os.path.exists(RULES_FILE):
        return {}
    with open(RULES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def section_agents(data):
    agents = data.get("agents", {})
    result = []
    for name, cfg in agents.items():
        if not isinstance(cfg, dict):
            continue
        entry = {
            "name": name,
            "cli": cfg.get("cli", ""),
            "driver": cfg.get("driver", "cli"),
            "capabilities": cfg.get("capabilities", []),
            "model": cfg.get("model", "unknown"),
            "timeout_s": cfg.get("timeout_s", 120),
            "description": cfg.get("description", ""),
        }
        if "wrapper" in cfg:
            entry["wrapper"] = cfg["wrapper"]
        if "command" in cfg:
            entry["command"] = cfg["command"]
        if "cli_args" in cfg:
            entry["cli_args"] = cfg["cli_args"]
        result.append(entry)
    return result


def section_routing(data):
    rt = data.get("routing", {})
    result = {}
    for key, entry in rt.items():
        if not isinstance(entry, dict):
            continue
        chain = [entry.get("primary")]
        for level in ("fallback", "tertiary"):
            val = entry.get(level)
            if val:
                if val not in chain:
                    chain.append(val)
        result[key] = {
            "primary": entry.get("primary", ""),
            "fallback": entry.get("fallback", ""),
            "tertiary": entry.get("tertiary", ""),
            "chain": chain,
        }
    return result


def section_loops(data):
    return data.get("loops", {})


def section_workflows(data):
    return data.get("workflows", {})


def section_rules():
    raw = load_rules()
    if not raw:
        return {}
    guards = raw.get("guards", {})
    result = {
        "max_retries": 2,
        "retry_backoff_ms": 2000,
        "timeout_s": 120,
    }
    retry = guards.get("retry", {})
    timeout_cfg = guards.get("timeout", {})
    if isinstance(retry, dict):
        result["max_retries"] = int(retry.get("max_attempts", 2))
        result["retry_backoff_ms"] = int(retry.get("backoff_ms", 2000))
    if isinstance(timeout_cfg, dict):
        result["timeout_s"] = int(timeout_cfg.get("default_s", 120))
    flat_rules = raw.get("rules", {})
    if isinstance(flat_rules, dict):
        for k in ("timeout_s", "max_retries", "retry_backoff_ms"):
            if k in flat_rules:
                result[k] = int(flat_rules[k])
    return result


def section_all(data):
    return {
        "agents": section_agents(data),
        "routing": section_routing(data),
        "loops": section_loops(data),
        "workflows": section_workflows(data),
        "rules": section_rules(),
    }


def agent_config(data, agent_name):
    agents = data.get("agents", {})
    cfg = agents.get(agent_name)
    if not cfg or not isinstance(cfg, dict):
        return {"error": f"agent '{agent_name}' not found"}
    return {
        "name": agent_name,
        "cli": cfg.get("cli", ""),
        "driver": cfg.get("driver", "cli"),
        "capabilities": cfg.get("capabilities", []),
        "model": cfg.get("model", "unknown"),
        "timeout_s": cfg.get("timeout_s", 120),
        "description": cfg.get("description", ""),
        "cli_args": cfg.get("cli_args", ""),
        "wrapper": cfg.get("wrapper", ""),
        "command": cfg.get("command", ""),
    }


def routing_key_config(data, key):
    rt = data.get("routing", {})
    entry = rt.get(key)
    if not entry or not isinstance(entry, dict):
        return {"error": f"routing key '{key}' not found", "primary": "claude", "fallback": "kimi", "tertiary": "qoder"}
    chain = [entry.get("primary")]
    for level in ("fallback", "tertiary"):
        val = entry.get(level)
        if val and val not in chain:
            chain.append(val)
    return {
        "primary": entry.get("primary", ""),
        "fallback": entry.get("fallback", ""),
        "tertiary": entry.get("tertiary", ""),
        "chain": chain,
    }


def parse_dag_file(path):
    """Parse a DAG workflow YAML file and return structured dict matching Read-DagYaml output."""
    if not os.path.exists(path):
        return {"error": f"DAG file not found: {path}"}
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    wf = raw.get("workflow", {})
    dag = {
        "id": wf.get("id", ""),
        "name": wf.get("name", ""),
        "version": str(wf.get("version", "1.0")),
        "defaults": {},
        "nodes": [],
    }

    # Parse defaults
    defaults = wf.get("defaults", {})
    if isinstance(defaults, dict):
        dag["defaults"] = {k: str(v) for k, v in defaults.items()}

    # Parse nodes
    for node in wf.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        n = {
            "id": node.get("id", ""),
            "type": node.get("type", "execute"),
            "agent": node.get("agent"),
            "agent_slot": node.get("agent_slot"),
            "depends_on": node.get("depends_on", []) or [],
            "params": node.get("params", {}) or {},
            "condition": node.get("condition"),
            "branches": node.get("branches", {}) or {},
            "output": node.get("output"),
        }
        # Ensure all values are strings in params/branches for PS compatibility
        n["params"] = {k: (str(v) if v is not None else "") for k, v in n["params"].items()}
        n["branches"] = {k: (str(v) if v is not None else "") for k, v in n["branches"].items()}
        dag["nodes"].append(n)

    return dag


def main():
    parser = argparse.ArgumentParser(description="MAOP unified config parser")
    parser.add_argument("--section", choices=["agents", "routing", "loops", "workflows", "rules", "all"], help="Output section")
    parser.add_argument("--agent", help="Output single agent config")
    parser.add_argument("--routing-key", help="Output single routing key entry")
    parser.add_argument("--dag", help="Parse a DAG workflow YAML file and output JSON")
    args = parser.parse_args()

    data = load_agents()

    try:
        if args.dag:
            result = parse_dag_file(args.dag)
        elif args.agent:
            result = agent_config(data, args.agent)
        elif args.routing_key:
            result = routing_key_config(data, args.routing_key)
        elif args.section == "agents":
            result = section_agents(data)
        elif args.section == "routing":
            result = section_routing(data)
        elif args.section == "loops":
            result = section_loops(data)
        elif args.section == "workflows":
            result = section_workflows(data)
        elif args.section == "rules":
            result = section_rules()
        elif args.section == "all":
            result = section_all(data)
        else:
            print(json.dumps({"error": "no section or agent specified"}))
            sys.exit(1)

        print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
