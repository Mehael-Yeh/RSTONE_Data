"""Explicit synthetic v2 publication fixtures; never imported by production.

No legacy source is admitted by this module: callers opt into constructing a
canonical model for an already-known positive test fixture.
"""
import re
import yaml
from rstone_data import recipe_codec as codec
from rstone_data.protocol import sha256


def typed(owner, title=None, rows=None, content=None, pre_table=None, associations=None, recipe_id=None):
    title = title or owner + "-底"
    return codec.emit({"schema": codec.SCHEMA, "recipeId": recipe_id or "SYNTH-"+sha256(owner.encode())[:24],
        "title": title, "classification": "main", "ownerProduct": owner,
        "associatedProducts": associations or [owner], "columns": codec.COLUMNS[0][:],
        "rows": rows or [["[["+owner+"]]", "40", "80", "", ""], ["水", "10", "20", "", ""]],
        "preTable": pre_table or [], "content": content or []})


def known_table(raw, title):
    """Explicit positive-fixture conversion; invalid fixture syntax is an error."""
    text=raw.decode(); metadata={}
    if text.startswith("---\n"):
        header,text=text[4:].split("\n---\n",1); metadata=yaml.safe_load(header) or {}
    lines=[line.lstrip(" ") for line in text.splitlines()]; headers=[i for i,l in enumerate(lines) if l.strip().startswith("| 原料编号") or l.startswith("|原料编号")]
    assert len(headers)==1, "not a known positive table fixture"
    start=headers[0]; columns=[s.strip() for s in lines[start].strip()[1:-1].split("|")]
    assert columns in codec.COLUMNS
    assert re.fullmatch(r"\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|",lines[start+1])
    rows=[]; end=start+2
    while end<len(lines) and lines[end].startswith("|"):
        row=[s.strip() for s in lines[end][1:-1].split("|")]; assert len(row)==5
        rows.append(row); end+=1
    refs=re.findall(r"\[\[([^\]]+)\]\]", metadata.get("关联产品", ""))
    owner=refs[0] if refs else re.findall(r"\[\[([^\]]+)\]\]",rows[0][0])[0]
    notes=[{"kind":"note","text":p} for p in "\n".join(lines[end:]).strip().split("\n\n") if p]
    model=codec.parse(typed(owner,title,rows,notes,associations=refs or [owner]))
    model["columns"]=columns
    return codec.emit(model)
