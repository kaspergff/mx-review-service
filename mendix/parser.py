"""
Mendix .mxunit BSON parser met volledige detail-extractie.
Gebaseerd op de mendix skill scripts (utils.py, parse_mxunit.py, microflow_analyzer.py).
"""

import uuid
import bson


# ---------------------------------------------------------------------------
# BSON utilities
# ---------------------------------------------------------------------------

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
    if 'VoidType' in t:        return 'void'
    if 'ObjectType' in t:      return type_obj.get('Entity', 'Object')
    if 'ListType' in t:        return f"List<{type_obj.get('Entity', '?')}>"
    if 'StringType' in t:      return f"String({type_obj.get('Length', '')})"
    if 'BooleanType' in t:     return 'Boolean'
    if 'IntegerType' in t:     return 'Integer'
    if 'LongType' in t:        return 'Long'
    if 'DecimalType' in t:     return 'Decimal'
    if 'DateTimeType' in t:    return 'DateTime'
    if 'EnumerationType' in t: return f"Enum<{type_obj.get('Enumeration', '?')}>"
    if 'AutoNumberType' in t:  return 'AutoNumber'
    if 'HashStringType' in t:  return 'HashString'
    if 'BinaryType' in t:      return 'Binary'
    return (t.replace('DomainModels$', '').replace('DataTypes$', '')
             .replace('AttributeType', '').replace('Type', ''))


def _filter(lst: list) -> list:
    return [i for i in lst if isinstance(i, dict)]


def parse_bytes(data: bytes) -> dict:
    """Parseer raw .mxunit bytes naar een geserialiseerd Python dict."""
    return _bson_to_serializable(bson.decode(data))


# ---------------------------------------------------------------------------
# Microflow action analysis (van microflow_analyzer.py)
# ---------------------------------------------------------------------------

def _extract_member_changes(action: dict) -> list:
    result = []
    for m in _filter(action.get('Items', [])):
        attr_full = m.get('Attribute', '') or m.get('Association', '')
        attr = attr_full.split('.')[-1] if attr_full else '?'
        val_str = str(m.get('Value', '')).strip()[:120]
        change_type = m.get('Type', 'Set')
        entry = {'attribute': attr, 'value': val_str}
        if change_type != 'Set':
            entry['type'] = change_type
        result.append(entry)
    return result


def _analyze_action(action: dict) -> dict:
    if not isinstance(action, dict):
        return {}
    atype = action.get('$Type', '?').replace('Microflows$', '')
    r = {'type': atype}

    if atype in ('CreateObjectAction', 'CreateChangeAction'):
        r['entity'] = action.get('Entity', '')
        r['variable'] = action.get('VariableName', '')
        r['commit'] = action.get('Commit', 'No')
        r['member_changes'] = _extract_member_changes(action)

    elif atype in ('ChangeObjectAction', 'ChangeAction'):
        r['variable'] = action.get('ChangeVariableName', action.get('VariableName', ''))
        r['commit'] = action.get('Commit', 'No')
        r['member_changes'] = _extract_member_changes(action)

    elif atype == 'DeleteAction':
        r['variable'] = action.get('VariableName', '')

    elif atype == 'RetrieveAction':
        src = action.get('RetrieveSource', {})
        if isinstance(src, dict):
            r['retrieve_source'] = src.get('$Type', '?').replace('Microflows$', '')
            r['entity'] = src.get('Entity', src.get('StartVariableName', ''))
            r['xpath'] = src.get('XpathConstraint', src.get('XPathConstraint', '')).replace('\n', ' ').strip()
            rng = src.get('Range', {})
            if isinstance(rng, dict):
                r['single_object'] = rng.get('SingleObject', False)
        r['variable'] = action.get('ResultVariableName', action.get('VariableName', ''))

    elif atype == 'MicroflowCallAction':
        mf_call = action.get('MicroflowCall', {})
        if isinstance(mf_call, dict):
            r['microflow'] = mf_call.get('Microflow', '')
            r['call_params'] = [
                {'param': p.get('Parameter', '?').split('.')[-1],
                 'value': str(p.get('Argument', '')).strip()}
                for p in _filter(mf_call.get('MicroflowCallParameterMappings', []))
            ]
        r['variable'] = action.get('VariableName', '')

    elif atype == 'JavaActionCallAction':
        ja_call = action.get('JavaActionCall', {})
        r['java_action'] = (
            action.get('JavaAction', '')
            or (ja_call.get('JavaAction', '') if isinstance(ja_call, dict) else '')
        )
        r['variable'] = action.get('ResultVariableName', action.get('VariableName', ''))

    elif atype == 'CommitAction':
        r['variable'] = action.get('CommitVariableName', action.get('VariableName', ''))
        r['with_events'] = action.get('WithEvents', False)
        r['refresh_in_client'] = action.get('RefreshInClient', False)

    elif atype == 'RollbackAction':
        r['variable'] = action.get('VariableName', '')

    elif atype == 'ShowPageAction':
        page_settings = action.get('PageSettings', {})
        r['page'] = page_settings.get('Page', '') if isinstance(page_settings, dict) else ''

    elif atype in ('CallRestServiceAction', 'CallWebServiceAction'):
        r['url'] = str(action.get('LocationTemplate', ''))[:120]
        r['http_method'] = action.get('HttpMethod', '')
        r['result_handling'] = action.get('ResultHandlingType', '')

    elif atype == 'LogMessageAction':
        mt = action.get('MessageTemplate', {})
        r['message'] = str(mt.get('Text', '') if isinstance(mt, dict) else '').strip()[:120]
        r['level'] = action.get('Level', '')

    elif atype == 'ShowMessageAction':
        mt = action.get('Template', {})
        if isinstance(mt, dict):
            text_obj = mt.get('Text', {})
            text = _get_text(text_obj) if isinstance(text_obj, dict) else str(text_obj)
        else:
            text = ''
        r['message'] = str(text).strip()[:120]
        r['blocking'] = action.get('Blocking', False)

    elif atype == 'ListOperationAction':
        op = action.get('ListOperation', {})
        if isinstance(op, dict):
            op_type = op.get('$Type', '').replace('Microflows$', '')
            r['list_operation'] = op_type
            r['list_variable'] = action.get('ListVariableName', '')
            # Filter/Find: expression staat op de operatie
            expr = op.get('Expression', '').replace('\n', ' ').strip()
            if expr:
                r['expression'] = expr
            r['variable'] = op.get('OutputVariableName', action.get('VariableName', ''))

    elif atype == 'AggregateListAction':
        r['list_variable'] = action.get('ListVariableName', '')
        r['aggregate_function'] = action.get('AggregateFunction', '')
        attr_full = action.get('AggregateVariableName', '')
        r['aggregate_attribute'] = attr_full.split('.')[-1] if attr_full else ''
        r['variable'] = action.get('VariableName', '')

    elif atype == 'CreateVariableAction':
        r['variable'] = action.get('VariableName', '')
        r['variable_type'] = _parse_type(action.get('VariableType', {}))
        r['expression'] = str(action.get('InitialValue', '')).replace('\n', ' ').strip()

    elif atype == 'ChangeVariableAction':
        r['variable'] = action.get('VariableName', '')
        r['expression'] = str(action.get('Value', '')).replace('\n', ' ').strip()

    elif atype == 'ChangeListAction':
        r['list_variable'] = action.get('ListVariableName', '')
        r['value'] = str(action.get('ContainedValue', '')).strip()
        r['list_change_type'] = action.get('Type', '')

    elif atype in ('NanoflowCallAction',):
        nf_call = action.get('NanoflowCall', {})
        if isinstance(nf_call, dict):
            r['nanoflow'] = nf_call.get('Nanoflow', '')
        r['variable'] = action.get('VariableName', '')

    return r


def _analyze_split(obj: dict) -> dict:
    cond = obj.get('SplitCondition', {})
    expression = cond.get('Expression', '') if isinstance(cond, dict) else ''
    return {
        'type': obj.get('$Type', '').replace('Microflows$', ''),
        'caption': obj.get('Caption', ''),
        'expression': expression,
    }


def _analyze_loop(obj: dict) -> dict:
    loop_src = obj.get('LoopSource', {})
    iterate_over = loop_src.get('ListVariableName', '') if isinstance(loop_src, dict) else ''
    iterator_name = loop_src.get('VariableName', '') if isinstance(loop_src, dict) else ''

    loop_col = obj.get('ObjectCollection', {})
    loop_objects = _filter(loop_col.get('Objects', [])) if isinstance(loop_col, dict) else []

    loop_actions, loop_splits = [], []
    for lo in loop_objects:
        lt = lo.get('$Type', '')
        if lt == 'Microflows$ActionActivity':
            action = lo.get('Action', {})
            if isinstance(action, dict):
                analyzed = _analyze_action(action)
                analyzed['caption'] = lo.get('Caption', '')
                error_handling = lo.get('ErrorHandlingType', 'Rollback')
                if error_handling and error_handling != 'Rollback':
                    analyzed['error_handling'] = error_handling
                loop_actions.append(analyzed)
        elif lt in ('Microflows$ExclusiveSplit', 'Microflows$InheritanceSplit'):
            loop_splits.append(_analyze_split(lo))

    return {
        'iterate_over': iterate_over,
        'iterator_name': iterator_name,
        'caption': obj.get('Caption', ''),
        'actions': loop_actions,
        'splits': loop_splits,
    }


def _analyze_flows(doc: dict, id_map: dict) -> list:
    def case_str(case_values):
        for cv in case_values:
            if not isinstance(cv, dict):
                continue
            ct = cv.get('$Type', '').replace('Microflows$', '')
            if ct == 'TrueCase':   return 'true'
            if ct == 'FalseCase':  return 'false'
            val = cv.get('Value', '')
            if val: return str(val)
        return ''

    result = []
    for f in _filter(doc.get('Flows', [])):
        origin = id_map.get(f.get('OriginPointer', ''), f.get('OriginPointer', '')[:8])
        dest = id_map.get(f.get('DestinationPointer', ''), f.get('DestinationPointer', '')[:8])
        case = case_str(f.get('CaseValues', []))
        if f.get('IsErrorHandler'):
            case = 'error'
        result.append({'from': origin, 'case': case, 'to': dest})
    return result


def _build_id_label_map(objects: list) -> dict:
    id_map, label_counts = {}, {}

    def add(obj):
        oid = obj.get('$ID', '')
        otype = obj.get('$Type', '').replace('Microflows$', '')
        caption = obj.get('Caption', '')
        action = obj.get('Action', {})
        atype = action.get('$Type', '').replace('Microflows$', '') if isinstance(action, dict) else ''
        # Bepaal label
        if caption and caption != 'Activity':
            label = caption
        elif atype:
            label = atype
        else:
            label = otype
        label_counts[label] = label_counts.get(label, 0) + 1
        if label_counts[label] > 1:
            label = f'{label}#{oid[:4]}'
        id_map[oid] = label

    for obj in objects:
        add(obj)
        if 'LoopedActivity' in obj.get('$Type', ''):
            loop_col = obj.get('ObjectCollection', {})
            for lo in _filter(loop_col.get('Objects', [])) if isinstance(loop_col, dict) else []:
                add(lo)
    return id_map


def _analyze_microflow(doc: dict) -> dict:
    doc_type = doc.get('$Type', '')
    flow_type = ('Nanoflow' if 'Nanoflow' in doc_type
                 else 'Rule' if 'Rule' in doc_type
                 else 'Microflow')

    obj_col = doc.get('ObjectCollection', {})
    objects = _filter(obj_col.get('Objects', [])) if isinstance(obj_col, dict) else []

    # Parameters: Mendix 10 in ObjectCollection, Mendix 9 op root
    param_objects = [o for o in objects if 'MicroflowParameter' in o.get('$Type', '')]
    if not param_objects:
        param_objects = _filter(doc.get('MicroflowParameters', []))
    params = [
        {'name': p.get('Name', '?'),
         'type': _parse_type(p.get('VariableType', {})) if isinstance(p.get('VariableType'), dict) else str(p.get('VariableType', '?')),
         'required': p.get('IsRequired', False)}
        for p in param_objects
    ]

    actions, splits, loops = [], [], []
    for obj in objects:
        otype = obj.get('$Type', '')
        if otype == 'Microflows$ActionActivity':
            action = obj.get('Action', {})
            if isinstance(action, dict):
                analyzed = _analyze_action(action)
                analyzed['caption'] = obj.get('Caption', '')
                analyzed['documentation'] = obj.get('Documentation', '')
                error_handling = obj.get('ErrorHandlingType', 'Rollback')
                if error_handling and error_handling != 'Rollback':
                    analyzed['error_handling'] = error_handling
                actions.append(analyzed)
        elif otype in ('Microflows$ExclusiveSplit', 'Microflows$InheritanceSplit'):
            splits.append(_analyze_split(obj))
        elif otype == 'Microflows$LoopedActivity':
            loops.append(_analyze_loop(obj))

    id_map = _build_id_label_map(objects)
    flows = _analyze_flows(doc, id_map)

    return {
        'type': flow_type,
        'name': doc.get('Name', ''),
        'documentation': doc.get('Documentation', ''),
        'return_type': _parse_type(doc.get('MicroflowReturnType', {})),
        'return_variable': doc.get('ReturnVariableName', ''),
        'parameters': params,
        'allowed_roles': [r for r in doc.get('AllowedModuleRoles', []) if isinstance(r, str)],
        'apply_entity_access': doc.get('ApplyEntityAccess', False),
        'excluded': doc.get('Excluded', False),
        'actions': actions,
        'splits': splits,
        'loops': loops,
        'flows': flows,
    }


# ---------------------------------------------------------------------------
# Domain model analysis
# ---------------------------------------------------------------------------

def _analyze_domain_model(doc: dict) -> dict:
    entities = _filter(doc.get('Entities', []))
    associations = _filter(doc.get('Associations', []))
    return {
        'type': 'DomainModel',
        'name': doc.get('Name', ''),
        'entities': [
            {
                'name': e.get('Name', '?'),
                'generalization': (e.get('Generalization', {}).get('GeneralizationName', '')
                                   if isinstance(e.get('Generalization'), dict) else ''),
                'attributes': [
                    {'name': a.get('Name', '?'), 'type': _parse_type(a.get('NewType', {}))}
                    for a in _filter(e.get('Attributes', []))
                ],
                'validation_rules': [
                    {'attribute': v.get('Attribute', '?'), 'rule': v.get('RuleInfo', {}).get('$Type', '?').replace('DomainModels$', '').replace('ValidationRuleInfo', '')}
                    for v in _filter(e.get('ValidationRules', []))
                ],
                'access_rules': [
                    {
                        'roles': [r for r in ar.get('ModuleRoles', []) if isinstance(r, str)],
                        'read': ar.get('AllowRead', False),
                        'create': ar.get('AllowCreate', False),
                        'delete': ar.get('AllowDelete', False),
                        'write': ar.get('AllowWrite', False),
                    }
                    for ar in _filter(e.get('AccessRules', []))
                ],
            }
            for e in entities
        ],
        'associations': [
            {
                'name': a.get('Name', '?'),
                'type': a.get('AssociationType', '?'),
                'owner': a.get('OwnerType', '?'),
                'parent': (a.get('ParentConnection', {}).get('Entity', '?')
                           if isinstance(a.get('ParentConnection'), dict) else '?'),
                'child': (a.get('ChildConnection', {}).get('Entity', '?')
                          if isinstance(a.get('ChildConnection'), dict) else '?'),
                'delete_parent': a.get('DeletingParent', False),
                'delete_child': a.get('DeletingChild', False),
            }
            for a in associations
        ],
    }


# ---------------------------------------------------------------------------
# Entry point: summarize any .mxunit document
# ---------------------------------------------------------------------------

def summarize(doc: dict) -> dict:
    """Analyseer een geparseerd BSON document naar een gestructureerde summary."""
    doc_type = doc.get('$Type', 'unknown')

    if 'DomainModels$DomainModel' in doc_type:
        return _analyze_domain_model(doc)

    if 'Microflows$Microflow' in doc_type or 'Microflows$Nanoflow' in doc_type or 'Microflows$Rule' in doc_type:
        return _analyze_microflow(doc)

    # Overige types: basale samenvatting
    result = {'type': doc_type, 'name': doc.get('Name', '')}

    if 'Forms$Page' in doc_type:
        title = doc.get('Title', {})
        result['title'] = _get_text(title) if isinstance(title, dict) else str(title)
        result['url'] = doc.get('Url', '')
        result['excluded'] = doc.get('Excluded', False)
        result['documentation'] = doc.get('Documentation', '')
        result['allowed_roles'] = [r for r in doc.get('AllowedModuleRoles', []) if isinstance(r, str)]

    elif 'Forms$Snippet' in doc_type:
        result['widget_count'] = len(_filter(doc.get('Widgets', [])))
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


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------

def _fmt_action(a: dict, prefix: str = '  ') -> list[str]:
    """Formatteer één actie als markdown-regels."""
    atype = a.get('type', '?')
    cap = a.get('caption', '')
    doc = a.get('documentation', '')
    lines = []

    label = f"**{atype}**"
    if cap and cap not in ('Activity', ''):
        label += f" `{cap}`"
    if doc:
        label += f" _{doc[:80]}_"

    details = []
    if a.get('entity'):
        details.append(f"entity: `{a['entity']}`")
    if a.get('xpath'):
        details.append(f"xpath: `{a['xpath']}`")
    if a.get('single_object'):
        details.append('→ eerste object')
    if a.get('variable'):
        details.append(f"→ `{a['variable']}`")
    if a.get('microflow'):
        details.append(f"roept aan: `{a['microflow']}`")
    if a.get('java_action'):
        details.append(f"java: `{a['java_action']}`")
    commit = str(a.get('commit', '') or '')
    if commit and commit not in ('No', 'False', 'None', ''):
        details.append(f"commit: {commit}")
    if a.get('with_events'):
        details.append('withEvents')
    if a.get('refresh_in_client'):
        details.append('refresh')
    if a.get('http_method') and a.get('url'):
        details.append(f"{a['http_method']} `{a['url']}`")
    if a.get('result_handling'):
        details.append(f"resultHandling: {a['result_handling']}")
    if a.get('message'):
        level = f" [{a['level']}]" if a.get('level') else ''
        details.append(f"bericht: \"{a['message']}\"{level}")
    if a.get('page'):
        details.append(f"pagina: `{a['page']}`")
    if a.get('expression'):
        details.append(f"expressie: `{a['expression']}`")
    if a.get('list_operation'):
        details.append(f"operatie: {a['list_operation']}")
    if a.get('list_variable'):
        details.append(f"lijst: `{a['list_variable']}`")
    if a.get('aggregate_function'):
        attr = a.get('aggregate_attribute', '')
        details.append(f"{a['aggregate_function']}(`{attr}`)")
    if a.get('variable_type'):
        details.append(f"type: {a['variable_type']}")
    if a.get('list_change_type'):
        details.append(f"lijstoperatie: {a['list_change_type']}")
    if a.get('nanoflow'):
        details.append(f"nanoflow: `{a['nanoflow']}`")
    if a.get('error_handling'):
        details.append(f"⚠ errorHandling: {a['error_handling']}")

    detail_str = '  |  '.join(details)
    lines.append(f"{prefix}- {label}{' — ' + detail_str if detail_str else ''}")

    # Call parameters
    for cp in a.get('call_params', []):
        lines.append(f"{prefix}  - param `{cp['param']}` = `{cp['value']}`")

    # Member changes
    for mc in a.get('member_changes', []):
        t = f" [{mc['type']}]" if mc.get('type') else ''
        lines.append(f"{prefix}  - `{mc['attribute']}`{t} = `{mc['value']}`")

    return lines


def format_summary(s: dict) -> str:
    """Formatteer een summary als leesbare markdown voor de LLM."""
    stype = s.get('type', 'unknown')
    lines = []

    # --- Microflow / Nanoflow / Rule ---
    if stype in ('Microflow', 'Nanoflow', 'Rule'):
        name = s.get('name', '?')
        lines.append(f"**{stype}:** `{name}`")
        if s.get('documentation'):
            lines.append(f"_{s['documentation'][:160]}_")
        lines.append(f"Return: `{s.get('return_type', 'void')}`"
                     + (f"  →  `{s['return_variable']}`" if s.get('return_variable') else ''))
        params = s.get('parameters', [])
        if params:
            p_str = ', '.join(f"`{p['name']}` ({p['type']})" for p in params)
            lines.append(f"Parameters: {p_str}")
        roles = s.get('allowed_roles', [])
        if roles:
            lines.append(f"Toegestane rollen: {', '.join(roles)}")
        else:
            lines.append("⚠ Toegestane rollen: **geen** (niet aanroepbaar vanuit UI/REST)")
        if s.get('apply_entity_access'):
            lines.append("Entity access: toegepast")
        if s.get('excluded'):
            lines.append("⚠ **Excluded** (inactief)")

        # Acties
        if s.get('actions'):
            lines.append("\n**Acties:**")
            for i, a in enumerate(s['actions'], 1):
                lines.append(f"  {i}.")
                lines.extend(_fmt_action(a, prefix='    '))

        # Splits
        if s.get('splits'):
            lines.append("\n**Splits:**")
            for sp in s['splits']:
                cap = sp.get('caption', '')
                expr = sp.get('expression', '')
                lines.append(f"  - {sp['type']}: `{expr}`" + (f" _{cap}_" if cap else ''))

        # Loops
        if s.get('loops'):
            lines.append("\n**Loops:**")
            for lp in s['loops']:
                cap = lp.get('caption', '') or ''
                over = lp.get('iterate_over', '?')
                it = lp.get('iterator_name', '')
                header = f"  - Loop over `{over}`"
                if it:
                    header += f" als `{it}`"
                if cap:
                    header += f" _{cap}_"
                lines.append(header)
                for la in lp.get('actions', []):
                    lines.extend(_fmt_action(la, prefix='      '))
                for ls in lp.get('splits', []):
                    lines.append(f"      - Split: `{ls.get('expression', '')}`")

        # Flows (control flow)
        if s.get('flows'):
            lines.append("\n**Control flow:**")
            for f in s['flows']:
                case = f.get('case', '')
                arrow = f"[{case}] " if case else ''
                lines.append(f"  - `{f['from']}` →{arrow} `{f['to']}`")

    # --- Domain model ---
    elif stype == 'DomainModel':
        lines.append(f"**Domain model:** `{s.get('name', '')}`")
        for e in s.get('entities', []):
            gen = f" extends `{e['generalization']}`" if e.get('generalization') else ''
            lines.append(f"\n**Entity** `{e['name']}`{gen}")
            for a in e.get('attributes', []):
                lines.append(f"  - `{a['name']}`: {a['type']}")
            for v in e.get('validation_rules', []):
                lines.append(f"  - Validatie `{v['attribute']}`: {v['rule']}")
            access = e.get('access_rules', [])
            if access:
                for ar in access:
                    roles_str = ', '.join(ar.get('roles', [])) or '(geen rollen)'
                    perms = []
                    if ar.get('read'):   perms.append('lezen')
                    if ar.get('write'):  perms.append('schrijven')
                    if ar.get('create'): perms.append('aanmaken')
                    if ar.get('delete'): perms.append('verwijderen')
                    perm_str = ', '.join(perms) or '(geen rechten)'
                    lines.append(f"  - Access rule [{roles_str}]: {perm_str}")
            else:
                lines.append("  ⚠ Geen access rules geconfigureerd")

        for a in s.get('associations', []):
            delete_warnings = []
            if a.get('delete_parent'): delete_warnings.append('cascade→parent')
            if a.get('delete_child'):  delete_warnings.append('cascade→child')
            delete_str = f"  ⚠ delete: {', '.join(delete_warnings)}" if delete_warnings else ''
            lines.append(f"- Associatie `{a['name']}` ({a['type']}): `{a['parent']}` → `{a['child']}`{delete_str}")

    # --- Page ---
    elif 'Forms$Page' in stype:
        lines.append(f"**Page:** `{s.get('name', '')}`")
        if s.get('title'):
            lines.append(f"Titel: {s['title']}")
        if s.get('url'):
            lines.append(f"URL: `{s['url']}`")
        if s.get('excluded'):
            lines.append("⚠ Excluded")
        if s.get('documentation'):
            lines.append(f"_{s['documentation'][:120]}_")
        roles = s.get('allowed_roles', [])
        if roles:
            lines.append(f"Toegestane rollen: {', '.join(roles)}")
        else:
            lines.append("⚠ Geen toegestane rollen (pagina niet bereikbaar)")

    # --- Overige ---
    elif 'Enumerations$Enumeration' in stype:
        lines.append(f"**Enumeratie:** `{s.get('name', '')}`")
        lines.append(f"Waarden: {', '.join(s.get('values', []))}")

    elif 'Constants$Constant' in stype:
        lines.append(f"**Constante:** `{s.get('name', '')}`")
        lines.append(f"Type: {s.get('const_type', '?')}  |  Default: `{s.get('default_value', '')}`")
        if s.get('documentation'):
            lines.append(f"_{s['documentation'][:120]}_")

    elif 'Forms$Snippet' in stype:
        lines.append(f"**Snippet:** `{s.get('name', '')}` ({s.get('widget_count', 0)} widgets)")

    elif 'Projects$ModuleImpl' in stype:
        name = s.get('name', '')
        if s.get('from_app_store'):
            lines.append(f"**Module:** `{name}` (AppStore v{s.get('app_store_version', '?')})")
        else:
            lines.append(f"**Module:** `{name}`")

    else:
        lines.append(f"**{stype}:** `{s.get('name', '')}`")

    return '\n'.join(lines)
