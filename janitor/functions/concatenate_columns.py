from typing import Hashable, List
import pandas_flavor as pf
import pandas as pd
from janitor.errors import JanitorError

from janitor.utils import deprecated_alias


@pf.register_dataframe_method
@deprecated_alias(columns="column_names")
def concatenate_columns(
    df: pd.DataFrame,
    column_names: List[Hashable],
    new_column_name,
    sep: str = "-",
    ignore_empty: bool = True,
) -> pd.DataFrame:
    """Concatenates the set of columns into a single column.

    Used to quickly generate an index based on a group of columns.

    This method mutates the original DataFrame.

    Functional usage syntax:

        df = concatenate_columns(df,
                                 column_names=['col1', 'col2'],
                                 new_column_name='id',
                                 sep='-')

    Method chaining syntax:

        df = (pd.DataFrame(...).
              concatenate_columns(column_names=['col1', 'col2'],
                                  new_column_name='id',
                                  sep='-'))

    :param df: A pandas DataFrame.
    :param column_names: A list of columns to concatenate together.
    :param new_column_name: The name of the new column.
    :param sep: The separator between each column's data.
    :param ignore_empty: Ignore null values if exists.
    :returns: A pandas DataFrame with concatenated columns.
    :raises JanitorError: if at least two columns are not provided
        within `column_names``.
    """
    if len(column_names) < 2:
        raise JanitorError("At least two columns must be specified")

    df[new_column_name] = (
        df[column_names].fillna("").astype(str).agg(sep.join, axis=1)
    )

    if ignore_empty:

        def remove_empty_string(x):
            return sep.join(x for x in x.split(sep) if x)

        df[new_column_name] = df[new_column_name].transform(
            remove_empty_string
        )

    return df
