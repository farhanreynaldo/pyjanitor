from pandas.core.construction import extract_array
from pandas.core.reshape.merge import _MergeOperation
from pandas.api.types import (
    is_datetime64_dtype,
    is_integer_dtype,
    is_float_dtype,
    is_string_dtype,
    is_categorical_dtype,
    is_extension_array_dtype,
)
import pandas_flavor as pf
import pandas as pd
from typing import Union
import operator
from janitor.utils import check, check_column
import numpy as np
from enum import Enum


@pf.register_dataframe_method
def conditional_join(
    df: pd.DataFrame,
    right: Union[pd.DataFrame, pd.Series],
    *conditions,
    how: str = "inner",
    sort_by_appearance: bool = False,
) -> pd.DataFrame:
    """

    This is a convenience function that operates similarly to ``pd.merge``,
    but allows joins on inequality operators,
    or a combination of equi and non-equi joins.

    If the join is solely on equality, `pd.merge` function
    is more efficient and should be used instead.

    If you are interested in nearest joins, or rolling joins,
    `pd.merge_asof` covers that. There is also the IntervalIndex,
    which is usually more efficient for range joins, especially if
    the intervals do not overlap.

    This function returns rows, if any, where values from `df` meet the
    condition(s) for values from `right`. The conditions are passed in
    as a variable argument of tuples, where the tuple is of
    the form `(left_on, right_on, op)`; `left_on` is the column
    label from `df`, `right_on` is the column label from `right`,
    while `op` is the operator.

    The operator can be any of `==`, `!=`, `<=`, `<`, `>=`, `>`.

    A binary search is used to get the relevant rows for non-equi joins;
    this avoids a cartesian join, and makes the process less memory intensive.

    For equi-joins, Pandas internal merge function (a hash join) is used.

    The join is done only on the columns.
    MultiIndex columns are not supported.

    For non-equi joins, only numeric and date columns are supported.

    Only `inner`, `left`, and `right` joins are supported.

    Functional usage syntax:

    ```python
        import pandas as pd
        import janitor as jn

        df = pd.DataFrame(...)
        right = pd.DataFrame(...)

        df = jn.conditional_join(
                df,
                right,
                (col_from_df, col_from_right, join_operator),
                (col_from_df, col_from_right, join_operator),
                ...,
                how = 'inner' # or left/right
                sort_by_appearance = True # or False
                )
    ```

    Method chaining syntax:

    .. code-block:: python

        df.conditional_join(
            right,
            (col_from_df, col_from_right, join_operator),
            (col_from_df, col_from_right, join_operator),
            ...,
            how = 'inner' # or left/right
            sort_by_appearance = True # or False
            )


    :param df: A Pandas DataFrame.
    :param right: Named Series or DataFrame to join to.
    :param conditions: Variable argument of tuple(s) of the form
        `(left_on, right_on, op)`, where `left_on` is the column
        label from `df`, `right_on` is the column label from `right`,
        while `op` is the operator. The operator can be any of
        `==`, `!=`, `<=`, `<`, `>=`, `>`.
    :param how: Indicates the type of join to be performed.
        It can be one of `inner`, `left`, `right`.
        Full join is not supported. Defaults to `inner`.
    :param sort_by_appearance: Default is `False`. If True,
        values from `right` that meet the join condition will be returned
        in the final dataframe in the same order
        that they were before the join.
    :returns: A pandas DataFrame of the two merged Pandas objects.
    """

    return _conditional_join_compute(
        df, right, conditions, how, sort_by_appearance
    )


def _conditional_join_compute(
    df: pd.DataFrame,
    right: pd.DataFrame,
    conditions: list,
    how: str,
    sort_by_appearance: bool,
) -> pd.DataFrame:
    """
    This is where the actual computation
    for the conditional join takes place.
    A pandas DataFrame is returned.
    """

    (
        df,
        right,
        conditions,
        how,
        sort_by_appearance,
    ) = _conditional_join_preliminary_checks(
        df, right, conditions, how, sort_by_appearance
    )

    eq_check = False
    less_great = False
    less_greater_types = less_than_join_types.union(greater_than_join_types)

    for condition in conditions:
        left_on, right_on, op = condition
        left_c = df[left_on]
        right_c = right[right_on]

        _conditional_join_type_check(left_c, right_c, op)

        if op == _JoinOperator.STRICTLY_EQUAL.value:
            eq_check = True
        elif op in less_greater_types:
            less_great = True

    df.index = pd.RangeIndex(start=0, stop=len(df))
    right.index = pd.RangeIndex(start=0, stop=len(right))

    if len(conditions) == 1:
        left_on, right_on, op = conditions[0]

        left_c = df[left_on]
        right_c = right[right_on]

        if eq_check & left_c.hasnans:
            left_c = left_c.dropna()
        if eq_check & right_c.hasnans:
            right_c = right_c.dropna()

        result = _generic_func_cond_join(left_c, right_c, op, 1)

        if result is None:
            return _create_conditional_join_empty_frame(df, right, how)

        left_c, right_c = result

        return _create_conditional_join_frame(
            df, right, left_c, right_c, how, sort_by_appearance
        )

    # multiple conditions
    if eq_check:
        result = _multiple_conditional_join_eq(df, right, conditions)
    elif less_great:
        result = _multiple_conditional_join_le_lt(df, right, conditions)
    else:
        result = _multiple_conditional_join_ne(df, right, conditions)

    if result is None:
        return _create_conditional_join_empty_frame(df, right, how)

    left_c, right_c = result

    return _create_conditional_join_frame(
        df, right, left_c, right_c, how, sort_by_appearance
    )


def _conditional_join_preliminary_checks(
    df: pd.DataFrame,
    right: Union[pd.DataFrame, pd.Series],
    conditions: tuple,
    how: str,
    sort_by_appearance: tuple,
) -> tuple:
    """
    Preliminary checks for conditional_join are conducted here.

    This function checks for conditions such as
    MultiIndexed dataframe columns,
    improper `suffixes` configuration,
    as well as unnamed Series.

    A tuple of
    (`df`, `right`, `left_on`, `right_on`, `operator`)
    is returned.
    """

    if df.empty:
        raise ValueError(
            """
            The dataframe on the left should not be empty.
            """
        )

    if isinstance(df.columns, pd.MultiIndex):
        raise ValueError(
            """
            MultiIndex columns are not
            supported for conditional_join.
            """
        )

    check("`right`", right, [pd.DataFrame, pd.Series])

    df = df.copy()
    right = right.copy()

    if isinstance(right, pd.Series):
        if not right.name:
            raise ValueError(
                """
                Unnamed Series are not supported
                for conditional_join.
                """
            )
        right = right.to_frame()

    if right.empty:
        raise ValueError(
            """
            The Pandas object on the right
            should not be empty.
            """
        )

    if isinstance(right.columns, pd.MultiIndex):
        raise ValueError(
            """
            MultiIndex columns are not supported
            for conditional joins.
            """
        )

    if not conditions:
        raise ValueError(
            """
            Kindly provide at least one join condition.
            """
        )

    for condition in conditions:
        check("condition", condition, [tuple])
        len_condition = len(condition)
        if len_condition != 3:
            raise ValueError(
                f"""
                condition should have only three elements.
                {condition} however is of length {len_condition}.
                """
            )

    for left_on, right_on, op in conditions:
        check("left_on", left_on, [str])
        check("right_on", right_on, [str])
        check("operator", op, [str])
        check_column(df, left_on)
        check_column(right, right_on)
        _check_operator(op)

    check("how", how, [str])

    join_types = {jointype.value for jointype in _JoinTypes}
    if how not in join_types:
        raise ValueError(f"`how` should be one of {', '.join(join_types)}.")

    check("sort_by_appearance", sort_by_appearance, [bool])

    return df, right, conditions, how, sort_by_appearance


def _conditional_join_type_check(
    left_column: pd.Series, right_column: pd.Series, op: str
) -> None:
    """
    Raise error if column type is not
    any of numeric or datetime.
    """

    # Allow merges on strings/categoricals,
    # but only on the `==` operator?
    permitted_types = {
        is_datetime64_dtype,
        is_integer_dtype,
        is_float_dtype,
        is_string_dtype,
        is_categorical_dtype,
    }
    for func in permitted_types:
        if func(left_column):
            break
    else:
        raise ValueError(
            """
            conditional_join only supports
            string, category, integer,
            float or date dtypes.
            """
        )
    cols = (left_column, right_column)
    for func in permitted_types:
        if all(map(func, cols)):
            break
    else:
        raise ValueError(
            f"""
             Both columns should have the same type.
             `{left_column.name}` has {left_column.dtype} type;
             `{right_column.name}` has {right_column.dtype} type.
             """
        )

    if (
        is_categorical_dtype(left_column)
        and op != _JoinOperator.STRICTLY_EQUAL.value
    ):
        raise ValueError(
            """
            For categorical columns,
            only the `==` operator is supported.
            """
        )

    if (
        is_string_dtype(left_column)
        and op != _JoinOperator.STRICTLY_EQUAL.value
    ):
        raise ValueError(
            """
            For string columns,
            only the `==` operator is supported.
            """
        )

    return None


def _generic_func_cond_join(
    left_c: pd.Series, right_c: pd.Series, op: str, len_conditions: int
):
    """
    Generic function to call any of the individual functions
    (_less_than_indices, _greater_than_indices, _equal_indices,
    or _not_equal_indices).
    """
    strict = False

    if op in {
        _JoinOperator.GREATER_THAN.value,
        _JoinOperator.LESS_THAN.value,
        _JoinOperator.NOT_EQUAL.value,
    }:
        strict = True

    if op in less_than_join_types:
        return _less_than_indices(left_c, right_c, strict, len_conditions)
    elif op in greater_than_join_types:
        return _greater_than_indices(left_c, right_c, strict, len_conditions)
    elif op == _JoinOperator.NOT_EQUAL.value:
        return _not_equal_indices(left_c, right_c)
    else:
        return _equal_indices(left_c, right_c, len_conditions)


def _multiple_conditional_join_eq(
    df: pd.DataFrame, right: pd.DataFrame, conditions: list
) -> tuple:
    """
    Get indices for multiple conditions,
    if any of the conditions has an `==` operator.

    Returns a tuple of (df_index, right_index)
    """

    eq_cond = [
        cond
        for cond in conditions
        if cond[-1] == _JoinOperator.STRICTLY_EQUAL.value
    ]
    rest = [
        cond
        for cond in conditions
        if cond[-1] != _JoinOperator.STRICTLY_EQUAL.value
    ]

    # get rid of nulls, if any
    if len(eq_cond) == 1:
        left_on, right_on, _ = eq_cond[0]
        left_c = df.loc[:, left_on]
        right_c = right.loc[:, right_on]

        if left_c.hasnans:
            left_c = left_c.dropna()
            df = df.loc[left_c.index]

        if right_c.hasnans:
            right_c = right_c.dropna()
            right = right.loc[right_c.index]

    else:
        left_on, right_on, _ = zip(*eq_cond)
        left_c = df.loc[:, [*left_on]]
        right_c = right.loc[:, [*right_on]]

        if left_c.isna().any(axis=None):
            left_c = left_c.dropna()
            df = df.loc[left_c.index]

        if right_c.isna().any(axis=None):
            right_c = right_c.dropna()
            right = right.loc[right_c.index]

    # get join indices
    # these are positional, not label indices
    result = _generic_func_cond_join(
        left_c, right_c, _JoinOperator.STRICTLY_EQUAL.value, 2
    )

    if result is None:
        return None

    df_index, right_index = result

    if not rest:
        return df.index[df_index], right.index[right_index]

    # non-equi conditions are present
    mask = None
    for left_on, right_on, op in rest:
        left_c = extract_array(df[left_on], extract_numpy=True)
        left_c = left_c[df_index]
        right_c = extract_array(right[right_on], extract_numpy=True)
        right_c = right_c[right_index]

        op = operator_map[op]
        if mask is None:
            mask = op(left_c, right_c)
        else:
            mask &= op(left_c, right_c)

    if not mask.any():
        return None

    df_index = df_index[mask]
    right_index = right_index[mask]

    return df.index[df_index], right.index[right_index]


def _multiple_conditional_join_ne(
    df: pd.DataFrame, right: pd.DataFrame, conditions: list
) -> tuple:
    """
    Get indices for multiple conditions,
    where all the operators are `!=`.

    Returns a tuple of (df_index, right_index)
    """

    first, *rest = conditions
    left_on, right_on, op = first
    left_c = df[left_on]
    right_c = right[right_on]
    result = _generic_func_cond_join(left_c, right_c, op, 1)
    if result is None:
        return None

    df_index, right_index = result

    mask = None
    for left_on, right_on, op in rest:
        left_c = df.loc[df_index, left_on]
        left_c = extract_array(left_c, extract_numpy=True)
        right_c = right.loc[right_index, right_on]
        right_c = extract_array(right_c, extract_numpy=True)
        op = operator_map[op]

        if mask is None:
            mask = op(left_c, right_c)
        else:
            mask &= op(left_c, right_c)

    if not mask.any():
        return None
    return df_index[mask], right_index[mask]


def _multiple_conditional_join_le_lt(
    df: pd.DataFrame, right: pd.DataFrame, conditions: list
) -> tuple:
    """
    Get indices for multiple conditions,
    if there is no `==` operator, and there is
    at least one `<`, `<=`, `>`, or `>=` operator.

    Returns a tuple of (df_index, right_index)
    """

    # find minimum df_index and right_index
    # aim is to reduce search space
    df_index = df.index
    right_index = right.index
    lt_gt = None
    less_greater_types = less_than_join_types.union(greater_than_join_types)
    for left_on, right_on, op in conditions:
        if op in less_greater_types:
            lt_gt = left_on, right_on, op
        # no point checking for `!=`, since best case scenario
        # they'll have the same no of rows for the less/greater operators
        elif op == _JoinOperator.NOT_EQUAL.value:
            continue

        left_c = df.loc[df_index, left_on]
        right_c = right.loc[right_index, right_on]

        result = _generic_func_cond_join(left_c, right_c, op, 2)

        if result is None:
            return None

        df_index, right_index, *_ = result

    # move le,lt,ge,gt to the fore
    # less rows to search, compared to !=
    if conditions[0][-1] not in less_greater_types:
        conditions = [*conditions]
        conditions.remove(lt_gt)
        conditions = [lt_gt] + conditions

    first, *rest = conditions
    left_on, right_on, op = first
    left_c = df.loc[df_index, left_on]
    right_c = right.loc[right_index, right_on]

    result = _generic_func_cond_join(left_c, right_c, op, 2)

    if result is None:
        return None

    df_index, right_index, search_indices, indices = result
    if op in less_than_join_types:
        low, high = search_indices, indices
    else:
        low, high = indices, search_indices

    first, *rest = rest
    left_on, right_on, op = first
    left_c = df.loc[df_index, left_on]
    left_c = extract_array(left_c, extract_numpy=True)
    right_c = right.loc[right_index, right_on]
    right_c = extract_array(right_c, extract_numpy=True)
    op = operator_map[op]
    index_df = []
    repeater = []
    index_right = []
    # offers a bit of a speed up, compared to broadcasting
    # we go through each search space, and keep only matching rows
    # constrained to just one loop;
    # if the join conditions are limited to two, this is helpful;
    # for more than two, then broadcasting kicks in after this step
    # running this within numba should offer more speed
    for indx, val, lo, hi in zip(df_index, left_c, low, high):
        search = right_c[lo:hi]
        indexer = right_index[lo:hi]
        mask = op(val, search)
        if not mask.any():
            continue
        # pandas boolean arrays do not play well with numpy
        # hence the conversion
        if is_extension_array_dtype(mask):
            mask = mask.to_numpy(dtype=bool, na_value=False)
        indexer = indexer[mask]
        index_df.append(indx)
        index_right.append(indexer)
        repeater.append(indexer.size)

    if not index_df:
        return None

    df_index = np.repeat(index_df, repeater)
    right_index = np.concatenate(index_right)

    if not rest:
        return df_index, right_index

    # blow it up
    mask = None
    for left_on, right_on, op in rest:
        left_c = df.loc[df_index, left_on]
        left_c = extract_array(left_c, extract_numpy=True)
        right_c = right.loc[right_index, right_on]
        right_c = extract_array(right_c, extract_numpy=True)
        op = operator_map[op]

        if mask is None:
            mask = op(left_c, right_c)
        else:
            mask &= op(left_c, right_c)

    if not mask.any():
        return None
    if is_extension_array_dtype(mask):
        mask = mask.to_numpy(dtype=bool, na_value=False)

    return df_index[mask], right_index[mask]


class _JoinOperator(Enum):
    """
    List of operators used in conditional_join.
    """

    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_THAN_OR_EQUAL = ">="
    LESS_THAN_OR_EQUAL = "<="
    STRICTLY_EQUAL = "=="
    NOT_EQUAL = "!="


def _create_conditional_join_empty_frame(
    df: pd.DataFrame, right: pd.DataFrame, how: str
):
    """
    Create final dataframe for conditional join,
    if there are no matches.
    """

    df.columns = pd.MultiIndex.from_product([["left"], df.columns])
    right.columns = pd.MultiIndex.from_product([["right"], right.columns])

    if how == _JoinTypes.INNER.value:
        df = df.dtypes.to_dict()
        right = right.dtypes.to_dict()
        df = {**df, **right}
        df = {key: pd.Series([], dtype=value) for key, value in df.items()}
        return pd.DataFrame(df)

    if how == _JoinTypes.LEFT.value:
        right = right.dtypes.to_dict()
        right = {
            key: float if dtype.kind == "i" else dtype
            for key, dtype in right.items()
        }
        right = {
            key: pd.Series([], dtype=value) for key, value in right.items()
        }
        right = pd.DataFrame(right)
        return df.join(right, how=how, sort=False)

    if how == _JoinTypes.RIGHT.value:
        df = df.dtypes.to_dict()
        df = {
            key: float if dtype.kind == "i" else dtype
            for key, dtype in df.items()
        }
        df = {key: pd.Series([], dtype=value) for key, value in df.items()}
        df = pd.DataFrame(df)
        return df.join(right, how=how, sort=False)


class _JoinTypes(Enum):
    """
    List of join types for conditional_join.
    """

    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"


def _create_conditional_join_frame(
    df: pd.DataFrame,
    right: pd.DataFrame,
    left_index: pd.Index,
    right_index: pd.Index,
    how: str,
    sort_by_appearance: bool,
):
    """
    Create final dataframe for conditional join,
    if there are matches.
    """
    if sort_by_appearance:
        sorter = np.lexsort((right_index, left_index))
        right_index = right_index[sorter]
        left_index = left_index[sorter]

    df.columns = pd.MultiIndex.from_product([["left"], df.columns])
    right.columns = pd.MultiIndex.from_product([["right"], right.columns])

    if how == _JoinTypes.INNER.value:
        df = df.loc[left_index]
        right = right.loc[right_index]
        df.index = pd.RangeIndex(start=0, stop=left_index.size)
        right.index = df.index
        return pd.concat([df, right], axis="columns", join=how, sort=False)

    if how == _JoinTypes.LEFT.value:
        right = right.loc[right_index]
        right.index = left_index
        return df.join(right, how=how, sort=False).reset_index(drop=True)

    if how == _JoinTypes.RIGHT.value:
        df = df.loc[left_index]
        df.index = right_index
        return df.join(right, how=how, sort=False).reset_index(drop=True)


less_than_join_types = {
    _JoinOperator.LESS_THAN.value,
    _JoinOperator.LESS_THAN_OR_EQUAL.value,
}
greater_than_join_types = {
    _JoinOperator.GREATER_THAN.value,
    _JoinOperator.GREATER_THAN_OR_EQUAL.value,
}


def _check_operator(op: str):
    """
    Check that operator is one of
    `>`, `>=`, `==`, `!=`, `<`, `<=`.

    Used in `conditional_join`.
    """
    sequence_of_operators = {op.value for op in _JoinOperator}
    if op not in sequence_of_operators:
        raise ValueError(
            f"""
             The conditional join operator
             should be one of {", ".join(sequence_of_operators)}
             """
        )


def _less_than_indices(
    left_c: pd.Series, right_c: pd.Series, strict: bool, len_conditions: int
) -> tuple:
    """
    Use binary search to get indices where left_c
    is less than or equal to right_c.

    If strict is True,then only indices
    where `left_c` is less than
    (but not equal to) `right_c` are returned.

    Returns a tuple of (left_c, right_c)
    """

    # no point going through all the hassle
    if left_c.min() > right_c.max():
        return None

    if right_c.hasnans:
        right_c = right_c.dropna()
    if not right_c.is_monotonic_increasing:
        right_c = right_c.sort_values()
    if left_c.hasnans:
        left_c = left_c.dropna()
    left_index = left_c.index.to_numpy(dtype=int)
    left_c = extract_array(left_c, extract_numpy=True)
    right_index = right_c.index.to_numpy(dtype=int)
    right_c = extract_array(right_c, extract_numpy=True)

    search_indices = right_c.searchsorted(left_c, side="left")
    # if any of the positions in `search_indices`
    # is equal to the length of `right_keys`
    # that means the respective position in `left_c`
    # has no values from `right_c` that are less than
    # or equal, and should therefore be discarded
    len_right = right_c.size
    rows_equal = search_indices == len_right

    if rows_equal.any():
        left_c = left_c[~rows_equal]
        left_index = left_index[~rows_equal]
        search_indices = search_indices[~rows_equal]

    if search_indices.size == 0:
        return None

    # the idea here is that if there are any equal values
    # shift upwards to the immediate next position
    # that is not equal
    if strict:
        rows_equal = right_c[search_indices]
        rows_equal = left_c == rows_equal
        # replace positions where rows are equal
        # with positions from searchsorted('right')
        # positions from searchsorted('right') will never
        # be equal and will be the furthermost in terms of position
        # example : right_c -> [2, 2, 2, 3], and we need
        # positions where values are not equal for 2;
        # the furthermost will be 3, and searchsorted('right')
        # will return position 3.
        if rows_equal.any():
            replacements = right_c.searchsorted(left_c, side="right")
            # now we can safely replace values
            # with strictly less than positions
            search_indices = np.where(rows_equal, replacements, search_indices)
        # check again if any of the values
        # have become equal to length of right_c
        # and get rid of them
        rows_equal = search_indices == len_right

        if rows_equal.any():
            left_c = left_c[~rows_equal]
            left_index = left_index[~rows_equal]
            search_indices = search_indices[~rows_equal]

    if search_indices.size == 0:
        return None

    indices = np.repeat(len_right, search_indices.size)

    if len_conditions > 1:
        return (left_index, right_index, search_indices, indices)

    positions = _interval_ranges(search_indices, indices)
    search_indices = indices - search_indices

    right_c = right_index[positions]
    left_c = left_index.repeat(search_indices)
    return left_c, right_c


def _greater_than_indices(
    left_c: pd.Series, right_c: pd.Series, strict: bool, len_conditions: int
) -> tuple:
    """
    Use binary search to get indices where left_c
    is greater than or equal to right_c.

    If strict is True,then only indices
    where `left_c` is greater than
    (but not equal to) `right_c` are returned.

    Returns a tuple of (left_c, right_c).
    """

    # quick break, avoiding the hassle
    if left_c.max() < right_c.min():
        return None

    if right_c.hasnans:
        right_c = right_c.dropna()
    if not right_c.is_monotonic_increasing:
        right_c = right_c.sort_values()
    if left_c.hasnans:
        left_c = left_c.dropna()
    left_index = left_c.index.to_numpy(dtype=int)
    left_c = extract_array(left_c, extract_numpy=True)
    right_index = right_c.index.to_numpy(dtype=int)
    right_c = extract_array(right_c, extract_numpy=True)

    search_indices = right_c.searchsorted(left_c, side="right")
    # if any of the positions in `search_indices`
    # is equal to 0 (less than 1), it implies that
    # left_c[position] is not greater than any value
    # in right_c
    rows_equal = search_indices < 1
    if rows_equal.any():
        left_c = left_c[~rows_equal]
        left_index = left_index[~rows_equal]
        search_indices = search_indices[~rows_equal]
    if search_indices.size == 0:
        return None

    # the idea here is that if there are any equal values
    # shift downwards to the immediate next position
    # that is not equal
    if strict:
        rows_equal = right_c[search_indices - 1]
        rows_equal = left_c == rows_equal
        # replace positions where rows are equal with
        # searchsorted('left');
        # however there can be scenarios where positions
        # from searchsorted('left') would still be equal;
        # in that case, we shift down by 1
        if rows_equal.any():
            replacements = right_c.searchsorted(left_c, side="left")
            # return replacements
            # `left` might result in values equal to len right_c
            replacements = np.where(
                replacements == right_c.size, replacements - 1, replacements
            )
            # now we can safely replace values
            # with strictly greater than positions
            search_indices = np.where(rows_equal, replacements, search_indices)
        # any value less than 1 should be discarded
        rows_equal = search_indices < 1
        if rows_equal.any():
            left_c = left_c[~rows_equal]
            left_index = left_index[~rows_equal]
            search_indices = search_indices[~rows_equal]

    if search_indices.size == 0:
        return None

    indices = np.zeros(search_indices.size, dtype=np.int8)

    if len_conditions > 1:
        return (left_index, right_index, search_indices, indices)

    positions = _interval_ranges(indices, search_indices)
    right_c = right_index[positions]
    left_c = left_index.repeat(search_indices)
    return left_c, right_c


def _equal_indices(
    left_c: Union[pd.Series, pd.DataFrame],
    right_c: Union[pd.Series, pd.DataFrame],
    len_conditions: int,
) -> tuple:
    """
    Use Pandas' merge internal functions
    to find the matches, if any.

    Returns a tuple of (left_c, right_c)
    """

    if isinstance(left_c, pd.Series):
        left_on = left_c.name
        right_on = right_c.name
    else:
        left_on = [*left_c.columns]
        right_on = [*right_c.columns]

    outcome = _MergeOperation(
        left=left_c,
        right=right_c,
        left_on=left_on,
        right_on=right_on,
        sort=False,
    )

    left_index, right_index = outcome._get_join_indexers()

    if not left_index.size > 0:
        return None

    if len_conditions > 1:
        return left_index, right_index

    return left_c.index[left_index], right_c.index[right_index]


def _not_equal_indices(left_c: pd.Series, right_c: pd.Series) -> tuple:
    """
    Use binary search to get indices where
    `left_c` is exactly  not equal to `right_c`.

    It is a combination of strictly less than
    and strictly greater than indices.

    Returns a tuple of (left_c, right_c)
    """

    dummy = np.array([], dtype=int)

    outcome = _less_than_indices(left_c, right_c, True, 1)

    if outcome is None:
        lt_left = dummy
        lt_right = dummy
    else:
        lt_left, lt_right = outcome

    outcome = _greater_than_indices(left_c, right_c, True, 1)

    if outcome is None:
        gt_left = dummy
        gt_right = dummy
    else:
        gt_left, gt_right = outcome

    if (not lt_left.size > 0) and (not gt_left.size > 0):
        return None
    left_c = np.concatenate([lt_left, gt_left])
    right_c = np.concatenate([lt_right, gt_right])

    return left_c, right_c


operator_map = {
    _JoinOperator.STRICTLY_EQUAL.value: operator.eq,
    _JoinOperator.LESS_THAN.value: operator.lt,
    _JoinOperator.LESS_THAN_OR_EQUAL.value: operator.le,
    _JoinOperator.GREATER_THAN.value: operator.gt,
    _JoinOperator.GREATER_THAN_OR_EQUAL.value: operator.ge,
    _JoinOperator.NOT_EQUAL.value: operator.ne,
}


def _interval_ranges(indices: np.ndarray, right: np.ndarray) -> np.ndarray:
    """
    Create `range` indices for each value in
    `right_keys` in  `_less_than_indices`
    and `_greater_than_indices`.

    It is faster than a list comprehension, especially
    for large arrays.

    code copied from Stack Overflow
    https://stackoverflow.com/a/47126435/7175713
    """
    cum_length = right - indices
    cum_length = cum_length.cumsum()
    # generate ones
    # note that cum_length[-1] is the total
    # number of index positions to be generated
    ids = np.ones(cum_length[-1], dtype=int)
    ids[0] = indices[0]
    # at each specific point in id, replace the value
    # so, we should have say 0, 1, 1, 1, 1, -5, 1, 1, 1, -3, ...
    # when a cumsum is implemented in the next line,
    # we get, 0, 1, 2, 3, 4, 0, 1, 2, 3, 0, ...
    # our ranges is obtained, with more efficiency
    # for larger arrays
    ids[cum_length[:-1]] = indices[1:] - right[:-1] + 1
    # the cumsum here gives us the same output as
    # [np.range(start, len_right) for start in search_indices]
    # but much faster
    return ids.cumsum()
