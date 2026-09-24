import pandas as pd
from run import config

def get_unique_values(df, column):
    """
    Returns sorted unique values for a column, filtering out nulls/empty strings.
    """
    if column not in df.columns:
        return []
    return sorted(df[column].dropna().unique().tolist())

def _mask_for_column(df, col, op, val, ftype):
    """Compute the boolean mask for a single real column. Returns None if the
    operator/type combination doesn't apply or the value can't be parsed.
    """
    if ftype == 'text':
        col_str = df[col].astype(str)
        if op == 'contains':
            return col_str.str.contains(val, case=False, na=False)
        if op == 'starts_with':
            return col_str.str.startswith(val, na=False)
        if op == 'ends_with':
            return col_str.str.endswith(val, na=False)
        if op == 'equals':
            return col_str == str(val)
        return None

    if ftype == 'number':
        try:
            num_val = float(val)
        except ValueError:
            return None
        if op == 'equals':
            return df[col] == num_val
        if op == '>':
            return df[col] > num_val
        if op == '<':
            return df[col] < num_val
        if op == '>=':
            return df[col] >= num_val
        if op == '<=':
            return df[col] <= num_val
        return None

    if ftype == 'select':
        # Multi-select (already implies OR between selections)
        if isinstance(val, list) and val:
            return df[col].isin(val)
        if not isinstance(val, list):
            return df[col] == val
        return None

    return None

def _mask_for_filter(df, col, op, val, ftype):
    """Compute the mask for one filter entry. A "combined:dep_col:arr_col" column id (used
    for the "Departure or Arrival X" filters) matches rows where either side matches.
    """
    if col.startswith('combined:'):
        _, dep_col, arr_col = col.split(':', 2)
        if dep_col not in df.columns or arr_col not in df.columns:
            return None
        mask_dep = _mask_for_column(df, dep_col, op, val, ftype)
        mask_arr = _mask_for_column(df, arr_col, op, val, ftype)
        if mask_dep is None:
            return mask_arr
        if mask_arr is None:
            return mask_dep
        return mask_dep | mask_arr

    if col not in df.columns:
        return None
    return _mask_for_column(df, col, op, val, ftype)

def _evaluate_node(df, node):
    """Recursively evaluate one node of a filter tree and return its boolean mask (or None
    if it contributes nothing, e.g. an empty/incomplete condition).

    A node is either a group - {'kind': 'group', 'logic': 'AND'|'OR', 'children': [...]} -
    whose children are combined left-to-right using each child's own 'logic' key, or a leaf
    condition - {'column', 'operator', 'value', 'type', 'logic'}. 'logic' says how the node
    combines with the *previous sibling* in its parent's children list, so precedence is
    entirely determined by the tree's nesting rather than a flat left-to-right fold.
    """
    if node.get('kind') == 'group':
        mask = None
        for child in node.get('children', []):
            child_mask = _evaluate_node(df, child)
            if child_mask is None:
                continue
            if mask is None:
                mask = child_mask
            elif child.get('logic', 'AND').upper() == 'OR':
                mask = mask | child_mask
            else:
                mask = mask & child_mask
        return mask

    col = node.get('column')
    op = node.get('operator')
    val = node.get('value')
    ftype = node.get('type', 'text')
    if not col or (val is None or val == ""):
        return None
    return _mask_for_filter(df, col, op, val, ftype)


def _flat_list_to_tree(filters):
    """Convert the legacy flat filter list into a group tree using standard boolean
    precedence (AND binds tighter than OR), e.g. "X OR Y AND Z" becomes X OR (Y AND Z).
    This is only a fallback for old-style callers; the GUI now sends an explicit nested
    tree so the user can pick either grouping intentionally.
    """
    or_groups = [[]]
    for i, f in enumerate(filters):
        logic = f.get('logic', 'AND').upper() if i > 0 else 'AND'
        if logic == 'OR':
            or_groups.append([])
        or_groups[-1].append(f)

    children = []
    for gi, group in enumerate(g for g in or_groups if g):
        group_logic = 'AND' if gi == 0 else 'OR'
        if len(group) == 1:
            leaf = dict(group[0])
            leaf['logic'] = group_logic
            children.append(leaf)
        else:
            sub_children = [dict(group[0], logic='AND')] + [dict(f, logic='AND') for f in group[1:]]
            children.append({'kind': 'group', 'logic': group_logic, 'children': sub_children})
    return {'kind': 'group', 'logic': 'AND', 'children': children}


def apply_filters(df, filters):
    """
    Applies a nested filter tree to the DataFrame.

    ``filters`` is normally a group node:
    {
        'kind': 'group',
        'logic': 'AND' | 'OR',  # how this group combines with its previous sibling
        'children': [ <group or leaf condition>, ... ]
    }
    A leaf condition looks like:
    {
        'column': 'col_name',
        'operator': '...',  # contains, starts_with, ends_with, equals, >, <, >=, <=
        'value': ...,        # standard value or list for 'select' (treated as OR/IN locally)
        'logic': 'AND' | 'OR' # how it combines with the previous sibling (default AND)
    }

    For backwards compatibility, a flat list of leaf conditions (old GUI format) or a
    {column: condition} dict (older legacy format) are also accepted and converted.
    """
    if df.empty or not filters:
        return df

    if isinstance(filters, dict) and filters.get('kind') != 'group':
        # Legacy {column: condition} dict.
        new_filters = []
        for col, condition in filters.items():
            filter_item = condition.copy()
            filter_item['column'] = col
            filter_item['logic'] = 'AND'
            new_filters.append(filter_item)
        filters = _flat_list_to_tree(new_filters)
    elif isinstance(filters, list):
        filters = _flat_list_to_tree(filters)

    mask = _evaluate_node(df, filters)
    if mask is None:
        return df

    return df[mask]
