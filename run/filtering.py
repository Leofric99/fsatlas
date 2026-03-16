import pandas as pd
from run import config

def get_unique_values(df, column):
    """
    Returns sorted unique values for a column, filtering out nulls/empty strings.
    """
    if column not in df.columns:
        return []
    return sorted(df[column].dropna().unique().tolist())

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

        if col not in df.columns:
            continue
            
        if val is None or val == "":
            continue

        this_mask = None
        
        # Calculate mask for this filter
        if ftype == 'text':
            col_str = df[col].astype(str)
            if op == 'contains':
                this_mask = col_str.str.contains(val, case=False, na=False)
            elif op == 'starts_with':
                this_mask = col_str.str.startswith(val, na=False)
            elif op == 'ends_with':
                this_mask = col_str.str.endswith(val, na=False)
            elif op == 'equals':
                this_mask = col_str == str(val)
        
        elif ftype == 'number':
            try:
                num_val = float(val)
                if op == 'equals':
                    this_mask = df[col] == num_val
                elif op == '>':
                    this_mask = df[col] > num_val
                elif op == '<':
                    this_mask = df[col] < num_val
                elif op == '>=':
                    this_mask = df[col] >= num_val
                elif op == '<=':
                    this_mask = df[col] <= num_val
            except ValueError:
                pass # Invalid number
        
        elif ftype == 'select': 
            # Multi-select (already implies OR between selections)
            if isinstance(val, list) and val:
                this_mask = df[col].isin(val)
            elif not isinstance(val, list):
                this_mask = df[col] == val

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
