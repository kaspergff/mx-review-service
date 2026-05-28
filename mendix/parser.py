"""
Mendix .mxunit BSON parser.
Gebaseerd op de mendix skill scripts (utils.py + parse_mxunit.py).
"""

import uuid
import bson


def _bson_to_serializable(obj):
    if isinstance(obj, bytes):
        if len(obj) == 16:
            try:
                return str(uuid.UUID(bytes=obj))
            except Exception:
                pass
        return obj.hex()
    elif isinstance(obj, dict):
        return {k: _bson_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_bson_to_serializable(i) for i in obj]
    if type(obj).__name__ == 'Int64':
        return int(obj)
    return obj


def _get_text(obj) -> str:
    if not isinstance(obj, dict):
        return str(obj) if obj else ''
    for item in obj.get('Items', []):
        if isinstance(item, dict):
            return item.get('Text', '')
    return ''


def _parse_type(type_obj: dict) -> str:
    if not isinstance(type_obj, dict):
        return str(type_obj) if type_obj else '?'
    t = type_obj.get('$Type', '')
    if 'VoidType' in t:       return 'void'
    if 'ObjectType' in t:     return type_obj.get('Entity', 'Object')
    if 'ListType' in t:       return f"List<{type_obj.get('Entity', '?')}>"
    if 'StringType' in t:     return f"String({type_obj.get('Length', '')})"
    if 'BooleanType' in t:    return 'Boolean'
    if 'IntegerType' in t:    return 'Integer'
    if 'LongType' in t:       return 'Long'
    if 'DecimalType' in t:    return 'Decimal'
    if 'DateTimeType' in t:   return 'DateTime'
    if 'EnumerationType' in t: return f"Enum<{type_obj.get('Enumeration', '?')}>"
    if 'AutoNumberType' in t: return 'AutoNumber'
    if 'HashStringType' in t: return 'HashString'
    if 'BinaryType' in t:     return 'Binary'
    return t.replace('DomainModels$', '').replace('DataTypes$', '').replace('AttributeType', '').replace('Type', '')


def _filter(lst: list) -> list:
    return [i for i in lst if isinstance(i, dict)]


def parse_bytes(data: bytes) -> dict:
    """Parseer raw .mxunit bytes naar een geserialiseerd Python dict."""
    return _bson_to_serializable(bson.decode(data))


def summarize(doc: dict) -> dict:
    """Zet een geparseerd BSON document om naar een compacte summary dict."""
    doc_type = doc.get('$Type', 'unknown')
    name = doc.get('Name', '')
    result = {'type': doc_type, 'name': name}

    if 'DomainModels$DomainModel' in doc_type:
        entities = _filter(doc.get('Entities', []))
        result['entity_count'] = len(entities)
        result['association_count'] = len(_filter(doc.get('Associations', [])))
        result['entities'] = [
            {
                'name': e.get('Name', '?'),
                'generalization': e.get('Generalization', {}).get('GeneralizationName', '')
                                  if isinstance(e.get('Generalization'), dict) else '',
                'attributes': [
                    {'name': a.get('Name', '?'), 'type': _parse_type(a.get('NewType', {}))}
                    for a in _filter(e.get('Attributes', []))
                ],
            }
            for e in entities
        ]
        result['associations'] = [
            {
                'name': a.get('Name', '?'),
                'type': a.get('AssociationType', '?'),
                'owner': a.get('OwnerType', '?'),
                'parent': a.get('ParentConnection', {}).get('Entity', '?') if isinstance(a.get('ParentConnection'), dict) else '?',
                'child': a.get('ChildConnection', {}).get('Entity', '?') if isinstance(a.get('ChildConnection'), dict) else '?',
                'delete_parent': a.get('DeletingParent', False),
                'delete_child': a.get('DeletingChild', False),
            }
            for a in _filter(doc.get('Associations', []))
        ]

    elif 'Microflows$Microflow' in doc_type or 'Microflows$Nanoflow' in doc_type:
        params = _filter(doc.get('MicroflowParameters', []))
        obj_col = doc.get('ObjectCollection', {})
        objects = _filter(obj_col.get('Objects', [])) if isinstance(obj_col, dict) else []
        actions = [o for o in objects if o.get('$Type') == 'Microflows$ActionActivity']
        result['return_type'] = _parse_type(doc.get('MicroflowReturnType', {}))
        result['documentation'] = doc.get('Documentation', '')
        result['parameters'] = [
            {'name': p.get('Name', '?'), 'type': _parse_type(p.get('VariableType', {}))}
            for p in params
        ]
        result['allowed_roles'] = [r for r in doc.get('AllowedModuleRoles', []) if isinstance(r, str)]
        result['actions'] = [
            {
                'type': a.get('Action', {}).get('$Type', '?').replace('Microflows$', '')
                        if isinstance(a.get('Action'), dict) else '?',
                'caption': a.get('Caption', ''),
            }
            for a in actions
        ]

    elif 'Forms$Page' in doc_type:
        title = doc.get('Title', {})
        result['title'] = _get_text(title) if isinstance(title, dict) else str(title)
        result['url'] = doc.get('Url', '')
        result['excluded'] = doc.get('Excluded', False)
        result['documentation'] = doc.get('Documentation', '')
        result['allowed_roles'] = [r for r in doc.get('AllowedModuleRoles', []) if isinstance(r, str)]

    elif 'Forms$Snippet' in doc_type:
        widgets = _filter(doc.get('Widgets', []))
        result['widget_count'] = len(widgets)
        result['documentation'] = doc.get('Documentation', '')

    elif 'Enumerations$Enumeration' in doc_type:
        result['values'] = [v.get('Name', '?') for v in _filter(doc.get('Values', []))]

    elif 'Constants$Constant' in doc_type:
        result['const_type'] = _parse_type(doc.get('Type', {}))
        result['default_value'] = doc.get('DefaultValue', '')
        result['documentation'] = doc.get('Documentation', '')

    elif 'Projects$ModuleImpl' in doc_type:
        result['from_app_store'] = doc.get('FromAppStore', False)
        result['app_store_version'] = doc.get('AppStoreVersion', '')

    return result


def format_summary(s: dict) -> str:
    """Formatteer een summary dict als leesbare markdown voor de LLM."""
    doc_type = s.get('type', 'unknown')
    name = s.get('name', '(onbekend)')
    short_type = doc_type.split('$')[-1] if '$' in doc_type else doc_type
    lines = [f"**{short_type}:** `{name}`"]

    if doc_str := s.get('documentation', ''):
        lines.append(f"*Documentatie:* {doc_str[:120]}")

    if 'DomainModels$DomainModel' in doc_type:
        lines.append(f"Entiteiten: {s.get('entity_count', 0)}  |  Associaties: {s.get('association_count', 0)}")
        for e in s.get('entities', []):
            gen = f" extends {e['generalization']}" if e.get('generalization') else ''
            lines.append(f"- Entity `{e['name']}`{gen}")
            for a in e.get('attributes', []):
                lines.append(f"  - `{a['name']}`: {a['type']}")
        for a in s.get('associations', []):
            delete_info = []
            if a.get('delete_parent'):
                delete_info.append('cascade→parent')
            if a.get('delete_child'):
                delete_info.append('cascade→child')
            delete_str = f" ⚠ delete: {', '.join(delete_info)}" if delete_info else ''
            lines.append(f"- Association `{a['name']}` ({a['type']}): {a['parent']} → {a['child']}{delete_str}")

    elif 'Microflows$' in doc_type or 'Nanoflows$' in doc_type:
        lines.append(f"Return: `{s.get('return_type', 'void')}`")
        roles = s.get('allowed_roles', [])
        if roles:
            lines.append(f"Toegestane rollen: {', '.join(roles)}")
        else:
            lines.append("Toegestane rollen: (geen — niet aanroepbaar vanuit UI/REST)")
        params = s.get('parameters', [])
        if params:
            param_str = ', '.join(f"{p['name']} ({p['type']})" for p in params)
            lines.append(f"Parameters: {param_str}")
        actions = s.get('actions', [])
        lines.append(f"Acties ({len(actions)}):")
        for a in actions:
            caption = f" [{a['caption']}]" if a.get('caption') and a['caption'] != 'Activity' else ''
            lines.append(f"  - {a['type']}{caption}")

    elif 'Forms$Page' in doc_type:
        lines.append(f"Titel: {s.get('title', '')}  |  URL: {s.get('url', '')}  |  Excluded: {s.get('excluded', False)}")
        roles = s.get('allowed_roles', [])
        if roles:
            lines.append(f"Toegestane rollen: {', '.join(roles)}")
        else:
            lines.append("Toegestane rollen: (geen — pagina niet bereikbaar)")

    elif 'Forms$Snippet' in doc_type:
        lines.append(f"Widgets: {s.get('widget_count', 0)}")

    elif 'Enumerations$Enumeration' in doc_type:
        lines.append(f"Waarden: {', '.join(s.get('values', []))}")

    elif 'Constants$Constant' in doc_type:
        lines.append(f"Type: {s.get('const_type', '?')}  |  Default: {s.get('default_value', '')}")

    elif 'Projects$ModuleImpl' in doc_type:
        if s.get('from_app_store'):
            lines.append(f"AppStore module versie {s.get('app_store_version', '?')}")

    return '\n'.join(lines)
