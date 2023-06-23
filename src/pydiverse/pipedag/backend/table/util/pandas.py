from __future__ import annotations

from enum import Enum, auto

import numpy as np
import pandas as pd
import pyarrow as pa
import sqlalchemy as sa


class PandasDTypeBackend(str, Enum):
    PANDAS = "pandas"
    ARROW = "arrow"


class DType(Enum):
    """

    Type Translation:

        DType <-> SQLAlchemy
        DType <-> Pandas
        DType <-> Arrow

    """

    # Integer Types
    INT8 = auto()
    INT16 = auto()
    INT32 = auto()
    INT64 = auto()

    UINT8 = auto()
    UINT16 = auto()
    UINT32 = auto()
    UINT64 = auto()

    # Float Types
    FLOAT32 = auto()
    FLOAT64 = auto()

    # Date/Time
    DATE = auto()
    TIME = auto()
    DATETIME = auto()

    # Other
    STRING = auto()
    BOOLEAN = auto()

    @staticmethod
    def from_sql(type_) -> DType:
        if isinstance(type_, sa.SmallInteger):
            return DType.INT16
        if isinstance(type_, sa.BigInteger):
            return DType.INT64
        if isinstance(type_, sa.Integer):
            return DType.INT32
        if isinstance(type_, sa.Numeric):
            return DType.FLOAT64
        if isinstance(type_, sa.String):
            return DType.STRING
        if isinstance(type_, sa.Boolean):
            return DType.BOOLEAN
        if isinstance(type_, sa.Date):
            return DType.DATE
        if isinstance(type_, sa.Time):
            return DType.TIME
        if isinstance(type_, sa.DateTime):
            return DType.DATETIME

        raise TypeError

    @staticmethod
    def from_pandas(type_) -> DType:
        def is_np_dtype(type_, np_dtype):
            return pd.core.dtypes.common._is_dtype_type(
                type_, pd.core.dtypes.common.classes(np_dtype)
            )

        if pd.api.types.is_signed_integer_dtype(type_):
            if is_np_dtype(type_, np.int64):
                return DType.INT64
            elif is_np_dtype(type_, np.int32):
                return DType.INT32
            elif is_np_dtype(type_, np.int16):
                return DType.INT16
            elif is_np_dtype(type_, np.int8):
                return DType.INT8
            raise TypeError
        if pd.api.types.is_unsigned_integer_dtype(type_):
            if is_np_dtype(type_, np.uint64):
                return DType.UINT64
            elif is_np_dtype(type_, np.uint32):
                return DType.UINT32
            elif is_np_dtype(type_, np.uint16):
                return DType.UINT16
            elif is_np_dtype(type_, np.uint8):
                return DType.UINT8
            raise TypeError
        if pd.api.types.is_float_dtype(type_):
            if is_np_dtype(type_, np.float64):
                return DType.FLOAT64
            elif is_np_dtype(type_, np.float32):
                return DType.FLOAT32
            raise TypeError
        if pd.api.types.is_string_dtype(type_):
            # We reserve the use of the object column for string.
            return DType.STRING
        if pd.api.types.is_bool_dtype(type_):
            return DType.BOOLEAN
        if pd.api.types.is_datetime64_any_dtype(type_):
            return DType.DATETIME

        raise TypeError

    def to_sql(self):
        return {
            DType.INT8: sa.SmallInteger(),
            DType.INT16: sa.SmallInteger(),
            DType.INT32: sa.Integer(),
            DType.INT64: sa.BigInteger(),
            DType.UINT8: sa.SmallInteger(),
            DType.UINT16: sa.Integer(),
            DType.UINT32: sa.BigInteger(),
            DType.UINT64: sa.BigInteger(),
            DType.FLOAT32: sa.Float(),
            DType.FLOAT64: sa.Double(),
            DType.STRING: sa.String(),
            DType.BOOLEAN: sa.Boolean(),
            DType.DATE: sa.Date(),
            DType.TIME: sa.Time(),
            DType.DATETIME: sa.DateTime(),
        }[self]

    def to_pandas(self, backend: PandasDTypeBackend = PandasDTypeBackend.PANDAS):
        if backend == PandasDTypeBackend.PANDAS:
            return self.to_pandas_nullable()
        if backend == PandasDTypeBackend.ARROW:
            return pd.ArrowDtype(self.to_arrow())

    def to_pandas_nullable(self):
        return {
            DType.INT8: pd.Int8Dtype(),
            DType.INT16: pd.Int16Dtype(),
            DType.INT32: pd.Int32Dtype(),
            DType.INT64: pd.Int64Dtype(),
            DType.UINT8: pd.UInt8Dtype(),
            DType.UINT16: pd.UInt16Dtype(),
            DType.UINT32: pd.UInt32Dtype(),
            DType.UINT64: pd.UInt64Dtype(),
            DType.FLOAT32: pd.Float32Dtype(),
            DType.FLOAT64: pd.Float64Dtype(),
            DType.STRING: pd.StringDtype(),
            DType.BOOLEAN: pd.BooleanDtype(),
            DType.DATE: "datetime64[ns]",
            DType.TIME: "datetime64[ns]",  # TODO: Check if this is correct
            DType.DATETIME: "datetime64[ns]",
        }[self]

    def to_arrow(self):
        return {
            DType.INT8: pa.int8(),
            DType.INT16: pa.int16(),
            DType.INT32: pa.int32(),
            DType.INT64: pa.int64(),
            DType.UINT8: pa.uint8(),
            DType.UINT16: pa.uint16(),
            DType.UINT32: pa.uint32(),
            DType.UINT64: pa.uint64(),
            DType.FLOAT32: pa.float32(),
            DType.FLOAT64: pa.float64(),
            DType.STRING: pa.string(),
            DType.BOOLEAN: pa.bool_(),
            DType.DATE: pa.date32(),
            DType.TIME: pa.time32("ms"),
            DType.DATETIME: pa.timestamp("ms"),
        }[self]


def adjust_pandas_types(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalizes the dtypes of a dataframe to match those expected by the
    DType conversion enum.
    """
    df = df.copy(deep=False)
    for col in df:
        dtype = DType.from_pandas(df[col].dtype).to_pandas()
        df[col] = df[col].astype(dtype)
    return df
