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
    Applies a dictionary of filters to the DataFrame.
    filters: dict where keys are column names and values are filter conditions.
    
    Structure of a filter value:
    {
        'type': 'text' | 'number' | 'select',
        'operator': 'equals' | 'contains' | 'starts_with' | 'ends_with' | '>', '<', '>=', '<=',
        'value': ...
    }
    """
    if df.empty:
        return df

    filtered_df = df.copy()

    for col, condition in filters.items():
        if col not in filtered_df.columns:
            continue
            
        value = condition.get('value')
        op = condition.get('operator')
        ftype = condition.get('type')

        if value is None or value == "":
            continue

        if ftype == 'text':
            if op == 'contains':
                filtered_df = filtered_df[filtered_df[col].astype(str).str.contains(value, case=False, na=False)]
            elif op == 'starts_with':
                filtered_df = filtered_df[filtered_df[col].astype(str).str.startswith(value, na=False)]
            elif op == 'ends_with':
                filtered_df = filtered_df[filtered_df[col].astype(str).str.endswith(value, na=False)]
            elif op == 'equals':
                filtered_df = filtered_df[filtered_df[col].astype(str) == str(value)]
        
        elif ftype == 'number':
            try:
                num_val = float(value)
                if op == 'equals':
                    filtered_df = filtered_df[filtered_df[col] == num_val]
                elif op == '>':
                    filtered_df = filtered_df[filtered_df[col] > num_val]
                elif op == '<':
                    filtered_df = filtered_df[filtered_df[col] < num_val]
                elif op == '>=':
                    filtered_df = filtered_df[filtered_df[col] >= num_val]
                elif op == '<=':
                    filtered_df = filtered_df[filtered_df[col] <= num_val]
            except ValueError:
                pass # Ignore invalid number inputs
        
        elif ftype == 'select': 
            # Multi-select or single select
            if isinstance(value, list) and value:
                filtered_df = filtered_df[filtered_df[col].isin(value)]
            elif not isinstance(value, list):
                filtered_df = filtered_df[filtered_df[col] == value]

    return filtered_df
