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

def apply_filters(df, filters):
    """
    Applies a list of filter dictionaries to the DataFrame.
    filters: list of dicts.
    Each dict should look like:
    {
        'column': 'col_name',
        'operator': '...',  # contains, starts_with, ends_with, equals, >, <, >=, <=
        'value': ...,      # standard value or list for 'select' (treated as OR/IN locally)
        'logic': 'AND' | 'OR' # how to combine with previous results (default AND)
    }
    """
    if df.empty or not filters:
        return df

    # If filters is a dict (legacy support - though we're updating GUI), convert to list
    if isinstance(filters, dict):
        new_filters = []
        for col, condition in filters.items():
            filter_item = condition.copy()
            filter_item['column'] = col
            filter_item['logic'] = 'AND'
            new_filters.append(filter_item)
        filters = new_filters

    current_mask = None

    for f in filters:
        col = f.get('column')
        op = f.get('operator')
        val = f.get('value')
        logic = f.get('logic', 'AND').upper()
        ftype = f.get('type', 'text') # default to text if missing

        if not col or (val is None or val == ""):
            continue

        this_mask = _mask_for_filter(df, col, op, val, ftype)

        # Combine with main mask
        if this_mask is not None:
            if current_mask is None:
                current_mask = this_mask
            else:
                if logic == 'OR':
                    current_mask = current_mask | this_mask
                else: # AND
                    current_mask = current_mask & this_mask

    if current_mask is None:
        return df
        
    return df[current_mask]
