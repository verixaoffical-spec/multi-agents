"""Rule based risk scoring. Pure functions, no network, easy to test and tune.

score  = size points + defect history points + sensitive area points
label  = Low (<=3), Medium (4-6), High (>=7), then raised by minimum labels:
         sensitive security/payment code -> at least High, sensitive data/config code -> at least Medium,
         a very large change -> High."""
import os
import re

HIGH_WORDS = {"auth", "authn", "authz", "authentication", "authorization", "login", "logout", "signin",
              "signup", "token", "jwt", "session", "password", "passwd", "credential", "oauth",
              "permission", "rbac", "secret", "encrypt", "crypto", "payment", "checkout", "billing",
              "charge", "refund", "invoice", "coupon", "discount", "price", "pricing", "stripe",
              "paypal", "wallet", "security"}
MEDIUM_WORDS = {"user", "account", "profile", "cart", "order", "inventory", "email", "upload",
                "notification", "admin", "config", "migration", "schema", "database", "db"}
WRAPPER_DIRS = {"src", "app", "apps", "lib", "packages"}
RANK = {"Low": 0, "Medium": 1, "High": 2}
LABELS = ["Low", "Medium", "High"]


def _extra(env: str) -> set[str]:
    return {w.strip().lower() for w in os.getenv(env, "").split(",") if w.strip()}


def _norm(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def tokens(text: str) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).lower()
    return {_norm(t) for t in re.split(r"[^a-z0-9]+", text) if t}


def is_test_or_doc(path: str) -> bool:
    p = path.lower()
    return (any(seg in p.split("/") for seg in ("test", "tests", "__tests__", "docs", "doc"))
            or ".spec." in p or ".test." in p or p.endswith((".md", ".txt", ".rst")))


def module_of(path: str) -> tuple[str, str]:
    """src/payments/tax.py -> ("payments", "src/payments"), README.md -> ("root", "")"""
    parts = path.split("/")
    if len(parts) == 1:
        return "root", ""
    if parts[0] in WRAPPER_DIRS and len(parts) > 2:
        return parts[1], "/".join(parts[:2])
    return parts[0], parts[0]


def size_points(files: int, lines: int) -> int:
    f = 3 if files >= 10 else 2 if files >= 4 else 1
    l = 3 if lines >= 500 else 2 if lines >= 100 else 1
    return f + l


def defect_points(defects: int) -> int:
    # capped at 3: a module with 20 defects is treated the same as 10
    return 0 if defects <= 0 else 3 if defects >= 10 else 2 if defects >= 4 else 1


def sensitivity(files: list[dict]) -> dict:
    """Looks at changed file paths, and at the changed lines for security or payment words.
    Test and doc files are ignored. Returns {"level": high|medium|none, "reasons": [...]}."""
    high_w, med_w = HIGH_WORDS | _extra("HIGH_RISK_KEYWORDS"), MEDIUM_WORDS | _extra("MEDIUM_RISK_KEYWORDS")
    high, medium = [], []
    for f in files:
        path = f["filename"]
        if is_test_or_doc(path):
            continue
        toks = tokens(path)
        if toks & high_w:
            high.append(f"{path} ({', '.join(sorted(toks & high_w))})")
            continue
        changed = " ".join(l[1:] for l in (f.get("patch") or "").splitlines()
                           if l[:1] in "+-" and not l.startswith(("+++", "---")))
        hits = tokens(changed) & high_w
        if len(hits) >= 2:                      # two different sensitive words in the changed code
            high.append(f"{path} (code mentions {', '.join(sorted(hits)[:4])})")
        elif toks & med_w:
            medium.append(f"{path} ({', '.join(sorted(toks & med_w))})")
    if high:
        return {"level": "high", "reasons": high[:3]}
    if medium:
        return {"level": "medium", "reasons": medium[:3]}
    return {"level": "none", "reasons": []}


def assess(files: list[dict], defects: int, defect_note: str = "") -> dict:
    lines = sum(f["additions"] + f["deletions"] for f in files)
    size = size_points(len(files), lines)
    d_pts = defect_points(defects)
    sens = sensitivity(files)
    s_pts = {"high": 2, "medium": 1, "none": 0}[sens["level"]]
    score = size + d_pts + s_pts
    base = "Low" if score <= 3 else "Medium" if score <= 6 else "High"

    floors = []
    if sens["level"] == "high":
        floors.append(("High", "sensitive security or payment code is touched"))
    elif sens["level"] == "medium":
        floors.append(("Medium", "user, data or config code is touched"))
    if size >= 6:
        floors.append(("High", "very large change (10+ files and 500+ lines)"))
    label, reason = base, None
    for fl, why in floors:
        if RANK[fl] > RANK[label]:
            label, reason = fl, why

    factors = [f"size: {len(files)} file(s), {lines} lines changed ({size} pts)"]
    factors.append(f"defect history: {defects} past fix(es) in this module ({d_pts} pts)" if defects
                   else "defect history: none found" + (f" ({defect_note})" if defect_note else ""))
    factors.append(f"sensitive area: {sens['level']}" + (f" - {'; '.join(sens['reasons'])}" if sens["reasons"] else "")
                   + f" ({s_pts} pts)")
    factors.append(f"score {score} gives {base}" + (f", raised to {label}: {reason}" if reason else ""))
    return {"score": score, "base_label": base, "risk_label": label, "risk_factors": factors,
            "size_points": size, "defect_points": d_pts, "sensitivity": sens["level"],
            "lines_changed": lines}
