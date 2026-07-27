"""
Validateur JSON-Schema minimaliste (sous-ensemble suffisant aux contrats d'agents).

Supporte : type (object/array/string/number/integer/boolean), required, properties,
additionalProperties, items, enum, minItems/maxItems, minLength, pattern,
minimum/maximum. Renvoie une LISTE d'erreurs localisées (chemin JSONPath-like),
jamais d'exception — la boucle de rétroaction les réinjecte dans le prompt.
Phase 1 : substituable au paquet `jsonschema` épinglé, contrat inchangé.
"""
from __future__ import annotations

import re
from typing import Any

_TYPES = {
    "object": dict, "array": list, "string": str,
    "number": (int, float), "integer": int, "boolean": bool,
}


def valider(schema: dict, obj: Any, chemin: str = "$") -> list[str]:
    erreurs: list[str] = []
    type_ = schema.get("type")
    if type_:
        pytype = _TYPES[type_]
        if not isinstance(obj, pytype) or (type_ in ("number", "integer")
                                           and isinstance(obj, bool)):
            return [f"{chemin} : type attendu {type_}, obtenu "
                    f"{type(obj).__name__}"]
    if "enum" in schema and obj not in schema["enum"]:
        erreurs.append(f"{chemin} : {obj!r} ∉ enum {schema['enum']}")

    if isinstance(obj, dict):
        for req in schema.get("required", []):
            if req not in obj:
                erreurs.append(f"{chemin}.{req} : champ requis absent")
        props = schema.get("properties", {})
        for cle, sous in props.items():
            if cle in obj:
                erreurs.extend(valider(sous, obj[cle], f"{chemin}.{cle}"))
        if schema.get("additionalProperties") is False:
            for cle in obj:
                if cle not in props:
                    erreurs.append(f"{chemin}.{cle} : propriété non autorisée")

    if isinstance(obj, list):
        items = schema.get("items")
        if items and "minItems" in schema and len(obj) < schema["minItems"]:
            erreurs.append(f"{chemin} : {len(obj)} éléments < minItems "
                           f"{schema['minItems']}")
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            erreurs.append(f"{chemin} : {len(obj)} éléments > maxItems "
                           f"{schema['maxItems']}")
        if items:
            for i, el in enumerate(obj):
                erreurs.extend(valider(items, el, f"{chemin}[{i}]"))

    if isinstance(obj, str):
        if "minLength" in schema and len(obj) < schema["minLength"]:
            erreurs.append(f"{chemin} : longueur < {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], obj):
            erreurs.append(f"{chemin} : ne satisfait pas /{schema['pattern']}/")

    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in schema and obj < schema["minimum"]:
            erreurs.append(f"{chemin} : {obj} < minimum {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            erreurs.append(f"{chemin} : {obj} > maximum {schema['maximum']}")
    return erreurs
