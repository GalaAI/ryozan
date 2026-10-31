#!/usr/bin/env python3
"""
+==============================================================+
|  PREFLIGHT DAEMON -- "The Watcher"                           |
|  GalaX Bestiary Class: Watcher (Monitoring-Derived)          |
|  Ailments: Migration Specter, Import Wraith,                 |
|            Dead Code Ghoul, Config Doppelganger              |
+==============================================================+

A sentinel that validates the Ryozan stack integrity.
Runs the Watcher Classification Protocol:
  DETECT -> IDENTIFY -> MEASURE -> CLASSIFY -> ASSESS -> LOG

Usage:
    python preflight.py                        Full scan (all checks)
    python preflight.py --check schema         Single check
    python preflight.py --fix                  Auto-generate ALTER statements
    python preflight.py --daemon               Background watcher (polls for changes)
    python preflight.py --daemon --interval 60 Poll every 60 seconds

Bestiary Encounters:
    Migration Specter   -- Schema drift between model and DB
    Import Wraith       -- Broken import chains
    Dead Code Ghoul     -- Unused functions haunting the codebase
    Config Doppelganger -- Orphaned endpoint files not in router
    Null Shade          -- Frontend calling non-existent backend routes
    Phantom             -- Missing dependencies
"""

import argparse
import ast
import asyncio
import hashlib
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

# -- Paths -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent
APP_DIR = PROJECT_ROOT / "app"
MODELS_DIR = APP_DIR / "models"
ENDPOINTS_DIR = APP_DIR / "api" / "v1" / "endpoints"
ROUTER_FILE = APP_DIR / "api" / "v1" / "router.py"
MAIN_FILE = PROJECT_ROOT / "main.py"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
FRONTEND_ROOT = REPO_ROOT / "app"
FRONTEND_API_FILE = FRONTEND_ROOT / "src" / "lib" / "api.ts"

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:galax2025@localhost:5432/nexus",
)

# -- ANSI Colors --------------------------------------------------
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
# Use ASCII fallback on Windows consoles that can't encode Unicode
try:
    "\u2713".encode(sys.stdout.encoding or "utf-8")
    CHECK = f"{GREEN}\u2713{RESET}"
    CROSS = f"{RED}\u2717{RESET}"
except (UnicodeEncodeError, LookupError):
    CHECK = f"{GREEN}[OK]{RESET}"
    CROSS = f"{RED}[FAIL]{RESET}"
WARN = f"{YELLOW}!{RESET}"

# -- Bestiary Daemon Catalog --------------------------------------
DAEMON_CATALOG = {
    "schema":     {"name": "Migration Specter",   "class": "Swarm",   "tier": "Phantom", "xp": 40},
    "imports":    {"name": "Import Wraith",        "class": "Swarm",   "tier": "Glitch",  "xp": 25},
    "dead":       {"name": "Dead Code Ghoul",      "class": "Lurker",  "tier": "Glitch",  "xp": 25},
    "router":     {"name": "Config Doppelganger",  "class": "Mimic",   "tier": "Worm",    "xp": 30},
    "migrations": {"name": "Migration Specter",    "class": "Swarm",   "tier": "Worm",    "xp": 35},
    "endpoints":  {"name": "Null Shade",           "class": "Shade",   "tier": "Worm",    "xp": 30},
    "deps":       {"name": "Phantom",              "class": "Phantom", "tier": "Phantom", "xp": 50},
}

TIERS = ["Glitch", "Worm", "Phantom", "Leviathan", "Entropy"]

# -- Counters -----------------------------------------------------
_pass = 0
_fail = 0
_warn = 0
_encounters: list[dict] = []

WATCH_GLOBS = (
    ("api", ("*.py", "*.sql", "*.ini")),
    ("app/src", ("*.ts", "*.tsx", "*.css")),
    ("app", ("package.json", "package-lock.json", "vite.config.ts", "tsconfig*.json", "index.html")),
    ("scripts", ("*.ps1", "*.cmd")),
)


def passed(msg: str):
    global _pass
    _pass += 1
    print(f"  {CHECK} {msg}")


def failed(msg: str):
    global _fail
    _fail += 1
    print(f"  {CROSS} {msg}")


def warned(msg: str):
    global _warn
    _warn += 1
    print(f"  {WARN} {YELLOW}{msg}{RESET}")


def header(title: str, check_key: str = ""):
    daemon = DAEMON_CATALOG.get(check_key, {})
    daemon_label = f" -- {daemon.get('name', '')}" if daemon else ""
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{daemon_label}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")


def iter_watch_files() -> list[Path]:
    files: set[Path] = {MAIN_FILE, REQUIREMENTS_FILE}
    for rel_root, patterns in WATCH_GLOBS:
        root = REPO_ROOT / rel_root
        if not root.exists():
            continue
        for pattern in patterns:
            files.update(root.rglob(pattern))
    return sorted(path for path in files if path.is_file())



def _record_encounter(check_key: str, issue_count: int):
    if issue_count == 0:
        return
    d = DAEMON_CATALOG.get(check_key, {})
    tier_idx = TIERS.index(d.get("tier", "Glitch"))
    if issue_count >= 10:
        tier_idx = min(tier_idx + 2, 4)
    elif issue_count >= 4:
        tier_idx = min(tier_idx + 1, 4)
    _encounters.append({
        "daemon": d.get("name", "Unknown"),
        "class": d.get("class", "?"),
        "tier": TIERS[tier_idx],
        "issues": issue_count,
        "xp_bounty": d.get("xp", 25) * issue_count,
    })


# =================================================================
#  MODEL PARSER (shared)
# =================================================================

class ModelColumn(NamedTuple):
    table: str
    column: str
    file: str


def _parse_model_columns() -> list[ModelColumn]:
    """Parse all SQLAlchemy model files and extract table names + mapped columns."""
    results = []
    for mf in MODELS_DIR.glob("*.py"):
        if mf.name == "__init__.py":
            continue
        try:
            tree = ast.parse(mf.read_text(encoding="utf-8"), filename=str(mf))
        except SyntaxError:
            warned(f"Syntax error parsing {mf.name}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            tablename = None
            columns = []
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == "__tablename__":
                            if isinstance(item.value, ast.Constant):
                                tablename = item.value.value
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    col_name = item.target.id
                    if item.value and isinstance(item.value, ast.Call):
                        func = item.value.func
                        func_name = ""
                        if isinstance(func, ast.Name):
                            func_name = func.id
                        elif isinstance(func, ast.Attribute):
                            func_name = func.attr
                        if func_name == "mapped_column":
                            if (item.value.args
                                    and isinstance(item.value.args[0], ast.Constant)
                                    and isinstance(item.value.args[0].value, str)):
                                columns.append(item.value.args[0].value)
                            else:
                                columns.append(col_name)
            if tablename and tablename.startswith("ryozan_"):
                for col in columns:
                    results.append(ModelColumn(tablename, col, mf.name))
    return results


# =================================================================
#  CHECK 1: Schema Drift Detector (Migration Specter)
# =================================================================

async def check_schema_drift(fix: bool = False):
    header("Schema Drift Detector", "schema")
    issues = 0
    try:
        import asyncpg
    except ImportError:
        failed("asyncpg not installed -- pip install asyncpg")
        _record_encounter("schema", 1)
        return

    try:
        url = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(url)
    except Exception as e:
        failed(f"Cannot connect to database: {e}")
        _record_encounter("schema", 1)
        return

    try:
        rows = await conn.fetch("""
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name LIKE 'ryozan_%%'
            ORDER BY table_name, ordinal_position
        """)
        db_columns: dict[str, set[str]] = {}
        for r in rows:
            db_columns.setdefault(r["table_name"], set()).add(r["column_name"])

        model_cols = _parse_model_columns()
        model_tables: dict[str, set[str]] = {}
        for mc in model_cols:
            model_tables.setdefault(mc.table, set()).add(mc.column)

        alter_statements = []

        for table, cols in sorted(model_tables.items()):
            if table not in db_columns:
                failed(f"Table {table} defined in models but NOT in database")
                issues += 1
                continue
            missing = cols - db_columns[table]
            present = cols & db_columns[table]
            for col in sorted(missing):
                failed(f"{table}.{col} -- in model, missing from DB")
                issues += 1
                alter_statements.append(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} TEXT;  -- CHECK TYPE"
                )
            for col in sorted(present):
                passed(f"{table}.{col}")

        for table, cols in sorted(db_columns.items()):
            if table not in model_tables:
                warned(f"Table {table} in DB but no model file found")
                continue
            extra = cols - model_tables[table]
            for col in sorted(extra):
                warned(f"{table}.{col} -- in DB but not in model (orphan?)")

        if fix and alter_statements:
            print(f"\n  {BOLD}Suggested ALTER statements:{RESET}")
            for stmt in alter_statements:
                print(f"    {stmt}")
        elif fix:
            passed("No ALTER statements needed")

    finally:
        await conn.close()
    _record_encounter("schema", issues)


# =================================================================
#  CHECK 2: Import Chain Validator (Import Wraith)
# =================================================================

def check_import_chains():
    header("Import Chain Validator", "imports")
    issues = 0
    py_files = list(APP_DIR.rglob("*.py"))
    import_pattern = re.compile(r"^from\s+(app\.\S+)\s+import\s+(.+)", re.MULTILINE)

    for pf in py_files:
        try:
            source = pf.read_text(encoding="utf-8")
        except Exception:
            warned(f"Cannot read {pf.relative_to(PROJECT_ROOT)}")
            continue

        for match in import_pattern.finditer(source):
            module_path = match.group(1)
            imported_names = [n.strip().split(" as ")[0].strip() for n in match.group(2).split(",")]

            parts = module_path.replace(".", os.sep)
            candidate_file = PROJECT_ROOT / (parts + ".py")
            candidate_pkg = PROJECT_ROOT / parts / "__init__.py"

            if not candidate_file.exists() and not candidate_pkg.exists():
                failed(f"{pf.relative_to(PROJECT_ROOT)}: module '{module_path}' not found")
                issues += 1
                continue

            target_file = candidate_file if candidate_file.exists() else candidate_pkg
            try:
                target_source = target_file.read_text(encoding="utf-8")
                target_tree = ast.parse(target_source)
            except Exception:
                warned(f"Cannot parse {target_file.relative_to(PROJECT_ROOT)}")
                continue

            defined_names = set()
            for node in ast.iter_child_nodes(target_tree):
                if isinstance(node, ast.ClassDef):
                    defined_names.add(node.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defined_names.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            defined_names.add(target.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    defined_names.add(node.target.id)
                elif isinstance(node, ast.ImportFrom):
                    if node.names:
                        for alias in node.names:
                            name = alias.asname if alias.asname else alias.name
                            defined_names.add(name)

            for name in imported_names:
                name = name.strip("()")
                if not name or name == "*":
                    continue
                if name in defined_names:
                    passed(f"{module_path}.{name}")
                else:
                    failed(f"{pf.relative_to(PROJECT_ROOT)}: '{name}' not found in {module_path}")
                    issues += 1

    _record_encounter("imports", issues)


# =================================================================
#  CHECK 3: Dead Code Scanner (Dead Code Ghoul)
# =================================================================

def check_dead_code():
    header("Dead Code Scanner", "dead")
    py_files = list(APP_DIR.rglob("*.py"))
    definitions: dict[str, str] = {}

    for pf in py_files:
        if pf.name == "__init__.py":
            continue
        try:
            tree = ast.parse(pf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                is_route = False
                for dec in node.decorator_list:
                    dec_name = ""
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                        dec_name = dec.func.value.id if isinstance(dec.func.value, ast.Name) else ""
                    elif isinstance(dec, ast.Attribute):
                        dec_name = dec.value.id if isinstance(dec.value, ast.Name) else ""
                    if dec_name == "router":
                        is_route = True
                        break
                if not is_route:
                    definitions[node.name] = str(pf.relative_to(PROJECT_ROOT))
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    definitions[node.name] = str(pf.relative_to(PROJECT_ROOT))

    all_source = ""
    for pf in py_files:
        try:
            all_source += pf.read_text(encoding="utf-8") + "\n"
        except Exception:
            continue
    try:
        all_source += MAIN_FILE.read_text(encoding="utf-8") + "\n"
    except Exception:
        pass

    dead = []
    for name, file in sorted(definitions.items()):
        matches = re.findall(r'\b' + re.escape(name) + r'\b', all_source)
        if len(matches) <= 1:
            dead.append((name, file))

    issues = len(dead)
    if dead:
        for name, file in dead:
            warned(f"{name} ({file}) -- never referenced elsewhere")
    else:
        passed("No dead code detected")
    passed(f"{len(definitions) - issues} functions/classes are referenced")
    _record_encounter("dead", issues)


# =================================================================
#  CHECK 4: Endpoint Coverage (Null Shade)
# =================================================================

def _extract_backend_routes() -> dict[str, list[str]]:
    routes: dict[str, list[str]] = {}
    if not ROUTER_FILE.exists():
        return routes
    route_pattern = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')
    for ef in ENDPOINTS_DIR.glob("*.py"):
        if ef.name == "__init__.py":
            continue
        try:
            source = ef.read_text(encoding="utf-8")
        except Exception:
            continue
        prefix_match = re.search(r'router\s*=\s*APIRouter\([^)]*prefix\s*=\s*["\']([^"\']*)["\']', source)
        prefix = prefix_match.group(1) if prefix_match else ""
        for match in route_pattern.finditer(source):
            method = match.group(1).upper()
            path = match.group(2)
            full_path = f"{prefix}{path}".rstrip("/") or "/"
            key = f"{method} {full_path}"
            routes.setdefault(key, []).append(ef.name)
    return routes


def _extract_frontend_calls() -> set[str]:
    calls = set()
    if not FRONTEND_API_FILE.exists():
        warned(f"Frontend api.ts not found: {FRONTEND_API_FILE}")
        return calls
    try:
        source = FRONTEND_API_FILE.read_text(encoding="utf-8")
    except Exception:
        return calls
    for match in re.finditer(r"request<[^>]*>\(\s*[`'\"]([^`'\"]+)[`'\"]", source):
        calls.add(match.group(1))
    for match in re.finditer(r"request<[^>]*>\(\s*`([^`]+)`", source):
        calls.add(re.sub(r'\$\{[^}]+\}', '{param}', match.group(1)))
    for match in re.finditer(r'fetch\(\s*`\$\{API_BASE\}([^`]+)`', source):
        calls.add(re.sub(r'\$\{[^}]+\}', '{param}', match.group(1)))
    return calls


def check_endpoint_coverage():
    header("Endpoint Coverage", "endpoints")
    issues = 0
    backend = _extract_backend_routes()
    frontend_paths = _extract_frontend_calls()

    if not backend:
        warned("No backend routes extracted")
        return
    if not frontend_paths:
        warned("No frontend API calls extracted")
        return

    backend_paths = set()
    for route_key in backend:
        _, path = route_key.split(" ", 1)
        backend_paths.add(path)

    normalized_fe = set()
    for p in frontend_paths:
        normalized_fe.add(p.split("?")[0].split("$")[0].rstrip("/") or "/")

    # Frontend calls to non-existent routes
    for fp in sorted(normalized_fe):
        fp_clean = fp.replace("{param}", "PLACEHOLDER").rstrip("/") or "/"
        matched = False
        for bp in backend_paths:
            bp_pattern = re.sub(r'\{[^}]+\}', '[^/]+', bp.rstrip("/") or "/")
            if re.fullmatch(bp_pattern, fp_clean):
                matched = True
                break
            if bp.rstrip("/") == fp_clean:
                matched = True
                break
            bp_base = bp.split("/{")[0] if "/{" in bp else bp
            if fp_clean.startswith(bp_base.rstrip("/")):
                matched = True
                break
        if not matched:
            failed(f"Frontend calls non-existent backend route: {fp}")
            issues += 1

    passed(f"{len(backend)} backend routes, {len(frontend_paths)} frontend calls")
    _record_encounter("endpoints", issues)


# =================================================================
#  CHECK 5: Stale Migration Detector (Migration Specter)
# =================================================================

def check_stale_migrations():
    """The schema has one path, Alembic: require a single head and no DDL in main.py."""
    header("Migration Chain", "migrations")
    issues = 0
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config()
        config.set_main_option("script_location", str(MAIN_FILE.parent / "alembic"))
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception as exc:  # noqa: BLE001 - report, do not crash the preflight
        failed(f"alembic chain unreadable: {exc}")
        _record_encounter("migrations", 1)
        return
    if len(heads) == 1:
        passed(f"alembic chain has one head: {heads[0]}")
    else:
        failed(f"alembic chain has {len(heads)} heads: {heads}; add a merge revision")
        issues += 1
    main_source = MAIN_FILE.read_text(encoding="utf-8") if MAIN_FILE.exists() else ""
    if re.search(r"(CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)|ALTER\s+TABLE)|create_all\(", main_source, re.I):
        failed("main.py issues schema DDL; schema changes belong in an Alembic revision")
        issues += 1
    _record_encounter("migrations", issues)


# =================================================================
#  CHECK 6: Dependency Check (Phantom)
# =================================================================

_PACKAGE_TO_IMPORT = {
    "python-jose": {"jose"}, "python-multipart": {"multipart"},
    "pydantic-settings": {"pydantic_settings"}, "email-validator": {"email_validator"},
    "google-auth": {"google.auth", "google.oauth2"},
    "google-api-python-client": {"googleapiclient"},
    "uvicorn[standard]": {"uvicorn"}, "sqlalchemy[asyncio]": {"sqlalchemy"},
    "fastapi": {"fastapi"}, "asyncpg": {"asyncpg"}, "bcrypt": {"bcrypt"},
    "pydantic": {"pydantic"}, "httpx": {"httpx"}, "aiosmtplib": {"aiosmtplib"},
    "stripe": {"stripe"}, "pywebpush": {"pywebpush"},
}
_IMPORT_TO_PACKAGE: dict[str, str] = {}
for _pkg, _imps in _PACKAGE_TO_IMPORT.items():
    for _imp in _imps:
        _IMPORT_TO_PACKAGE[_imp] = _pkg


def check_dependencies():
    header("Dependency Check", "deps")
    issues = 0
    if not REQUIREMENTS_FILE.exists():
        failed("requirements.txt not found")
        _record_encounter("deps", 1)
        return

    req_packages = set()
    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req_packages.add(re.split(r"[>=<!\[;]", line)[0].strip().lower())

    available_imports = set()
    for pkg in req_packages:
        clean = re.sub(r"\[.*\]", "", pkg)
        if pkg in _PACKAGE_TO_IMPORT:
            available_imports.update(_PACKAGE_TO_IMPORT[pkg])
        elif clean in _PACKAGE_TO_IMPORT:
            available_imports.update(_PACKAGE_TO_IMPORT[clean])
        else:
            available_imports.add(clean.replace("-", "_"))

    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
        "os", "sys", "re", "ast", "json", "uuid", "datetime", "pathlib",
        "typing", "collections", "functools", "itertools", "contextlib",
        "secrets", "hashlib", "base64", "io", "enum", "dataclasses",
        "logging", "traceback", "math", "copy", "time", "asyncio",
        "email", "html", "urllib", "http", "abc", "inspect", "string",
        "random", "struct", "csv", "tempfile", "mimetypes", "warnings",
    }

    third_party: dict[str, set[str]] = {}
    for pf in APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(pf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in stdlib and top != "app":
                        third_party.setdefault(top, set()).add(str(pf.relative_to(PROJECT_ROOT)))
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top not in stdlib and top != "app":
                    third_party.setdefault(top, set()).add(str(pf.relative_to(PROJECT_ROOT)))

    for imp, files in sorted(third_party.items()):
        found = imp in available_imports
        if not found:
            for avail in available_imports:
                if avail.startswith(imp + ".") or imp.startswith(avail.split(".")[0]):
                    found = True
                    break
        if not found and imp.lower().replace("_", "-") in req_packages:
            found = True
        if found:
            passed(f"{imp} -- in requirements.txt")
        else:
            failed(f"{imp} -- imported in {', '.join(sorted(files)[:3])} but NOT in requirements.txt")
            issues += 1

    _record_encounter("deps", issues)


# =================================================================
#  DAEMON MODE (Background Watcher)
# =================================================================

async def daemon_loop(interval: int = 30):
    print(f"\n{BOLD}  THE WATCHER AWAKENS{RESET}")
    print(f"{DIM}  Daemon mode -- scanning every {interval}s{RESET}")
    print(f"{DIM}  Watching: {REPO_ROOT}{RESET}")
    print(f"{DIM}  Ctrl+C to banish{RESET}\n")

    prev: dict[str, str] = {}
    first = True

    while True:
        curr: dict[str, str] = {}
        changed = False
        changed_files: list[str] = []
        for f in iter_watch_files():
            try:
                h = hashlib.md5(f.read_bytes()).hexdigest()
                k = str(f.relative_to(REPO_ROOT))
                curr[k] = h
                if not first and (k not in prev or prev[k] != h):
                    print(f"\n{YELLOW}  Change detected: {k}{RESET}")
                    changed = True
                    changed_files.append(k)
            except (OSError, PermissionError):
                pass

        if changed or first:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n{CYAN}{'=' * 60}{RESET}")
            print(f"{BOLD}  WATCHER SCAN -- {ts}{RESET}")
            print(f"{CYAN}{'=' * 60}{RESET}")
            await run_all(exit_on_fail=False)

        prev = curr
        first = False
        await asyncio.sleep(interval)


# =================================================================
#  RUNNER
# =================================================================

CHECKS = {
    "schema": lambda fix=False: check_schema_drift(fix),
    "imports": lambda fix=False: check_import_chains(),
    "dead": lambda fix=False: check_dead_code(),
    "endpoints": lambda fix=False: check_endpoint_coverage(),
    "migrations": lambda fix=False: check_stale_migrations(),
    "deps": lambda fix=False: check_dependencies(),
}


async def run_all(fix: bool = False, check: str | None = None, exit_on_fail: bool = True) -> dict[str, Any]:
    global _pass, _fail, _warn, _encounters
    _pass = _fail = _warn = 0
    _encounters = []

    print(f"\n{BOLD}PREFLIGHT DAEMON -- The Watcher{RESET}")
    print(f"{DIM}Protocol: DETECT -> IDENTIFY -> MEASURE -> CLASSIFY -> LOG{RESET}")
    print(f"Project: {PROJECT_ROOT}")
    print(f"Database: {DB_URL.split('@')[1] if '@' in DB_URL else DB_URL}")

    if check:
        if check not in CHECKS:
            print(f"{RED}Unknown check: {check}{RESET}")
            print(f"Available: {', '.join(CHECKS.keys())}")
            sys.exit(1)
        fn = CHECKS[check]
        if check == "schema":
            await fn(fix=fix)
        else:
            fn(fix=fix)
    else:
        await check_schema_drift(fix=fix)
        check_import_chains()
        check_dead_code()
        check_endpoint_coverage()
        check_stale_migrations()
        check_dependencies()

    # -- Summary with Bestiary encounter report --
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    total = _pass + _fail + _warn
    parts = []
    if _pass:
        parts.append(f"{GREEN}{_pass} passed{RESET}")
    if _fail:
        parts.append(f"{RED}{_fail} failed{RESET}")
    if _warn:
        parts.append(f"{YELLOW}{_warn} warnings{RESET}")

    status = f"{GREEN}ALL CLEAR{RESET}" if _fail == 0 else f"{RED}DAEMONS DETECTED{RESET}"
    print(f"  {BOLD}Result: {status}{RESET}  |  {' / '.join(parts)}  |  {total} checks")

    if _encounters:
        total_xp = sum(e["xp_bounty"] for e in _encounters)
        print(f"\n  {BOLD}Encounter Log:{RESET}")
        for enc in _encounters:
            print(f"    {RED}* {enc['daemon']} ({enc['class']}/{enc['tier']}) "
                  f"-- {enc['issues']} issues, {enc['xp_bounty']} XP bounty{RESET}")
        print(f"\n  {YELLOW}Total XP bounty if resolved: {total_xp} XP{RESET}")
    print()

    summary = {
        "pass": _pass,
        "fail": _fail,
        "warn": _warn,
        "encounters": _encounters,
        "status": "degraded" if _fail > 0 else "nominal",
    }

    if _fail > 0 and exit_on_fail:
        sys.exit(1)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Preflight Daemon -- The Watcher (GalaX Bestiary Entity)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
checks:
  schema      Schema drift (Migration Specter)
  imports     Import chains (Import Wraith)
  dead        Dead code (Dead Code Ghoul)
  endpoints   API contract (Null Shade)
  migrations  Stale migrations (Migration Specter)
  deps        Dependencies (Phantom)
""",
    )
    parser.add_argument("--check", choices=list(CHECKS.keys()), help="Run specific check")
    parser.add_argument("--fix", action="store_true", help="Generate ALTER statements for schema drift")
    parser.add_argument("--daemon", action="store_true", help="Run as background repo and app watcher")
    parser.add_argument("--interval", type=int, default=30, help="Daemon poll interval (seconds)")
    args = parser.parse_args()

    if args.daemon:
        try:
            asyncio.run(daemon_loop(args.interval))
        except KeyboardInterrupt:
            print(f"\n{DIM}The Watcher sleeps...{RESET}")
    else:
        asyncio.run(run_all(fix=args.fix, check=args.check))
