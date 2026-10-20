"""Dataset validation, schema extraction, and canonicalization."""

import csv
import io
import warnings

import pandas as pd

from ml_mcp.config import Settings, get_settings
from ml_mcp.domain.errors import DatasetValidationError
from ml_mcp.domain.value_objects import (
    ColumnSchema,
    DatasetFormat,
    DatasetSchema,
    FeatureType,
)
from ml_mcp.infrastructure.object_storage.s3 import S3StorageService


class DatasetValidator:
    """Performs strict validation and schema extraction on untrusted tabular datasets."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _infer_feature_type(self, series: pd.Series) -> FeatureType:
        """Map pandas/pyarrow series to standard FeatureType."""
        if pd.api.types.is_bool_dtype(series):
            return FeatureType.BOOLEAN
        elif pd.api.types.is_numeric_dtype(series):
            return FeatureType.NUMERIC
        elif pd.api.types.is_datetime64_any_dtype(series):
            return FeatureType.DATETIME
        elif pd.api.types.is_categorical_dtype(series):
            return FeatureType.CATEGORICAL
        else:
            # String / object column
            non_null = series.dropna()
            if len(non_null) > 0:
                unique_ratio = len(non_null.unique()) / len(non_null)
                # If unique ratio is low, treat as categorical; otherwise text
                if unique_ratio < 0.2 or len(non_null.unique()) <= 50:
                    return FeatureType.CATEGORICAL
            return FeatureType.TEXT

    def parse_dataframe(self, raw_bytes: bytes, data_format: DatasetFormat | str) -> pd.DataFrame:
        """Parse raw bytes into a pandas DataFrame, rejecting malformed inputs."""
        # 1. Size constraint
        if len(raw_bytes) > self.settings.security.max_dataset_upload_bytes:
            raise DatasetValidationError(
                f"Dataset size ({len(raw_bytes)} bytes) exceeds maximum permitted limit ({self.settings.security.max_dataset_upload_bytes} bytes)"
            )
        if len(raw_bytes) == 0:
            raise DatasetValidationError("Dataset upload is empty")

        fmt = str(data_format).lower()
        try:
            bio = io.BytesIO(raw_bytes)
            if fmt == DatasetFormat.CSV.value:
                # Deterministic check for consistent column counts across all rows
                try:
                    text_content = raw_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise DatasetValidationError(f"Malformed encoding: dataset must be valid UTF-8: {exc}") from exc

                reader = csv.reader(io.StringIO(text_content))
                try:
                    header = next(reader, None)
                except Exception as exc:
                    raise DatasetValidationError(f"Failed to parse dataset as csv: {exc}") from exc

                if header is None or len(header) == 0:
                    raise DatasetValidationError("Dataset upload is empty or has no header")

                expected_cols = len(header)
                for line_idx, row in enumerate(reader, start=2):
                    if not row:
                        continue
                    if len(row) != expected_cols:
                        raise DatasetValidationError(
                            f"Failed to parse dataset as csv: Row {line_idx} has {len(row)} columns, expected {expected_cols}"
                        )

                with warnings.catch_warnings():
                    warnings.simplefilter("error", pd.errors.ParserWarning)
                    df = pd.read_csv(bio, index_col=False)
            elif fmt == DatasetFormat.PARQUET.value:
                df = pd.read_parquet(bio)
            elif fmt == DatasetFormat.JSONL.value:
                df = pd.read_json(bio, lines=True)
            else:
                raise DatasetValidationError(f"Unsupported dataset format '{data_format}'. Allowed: csv, parquet, jsonl")
        except Exception as exc:
            raise DatasetValidationError(f"Failed to parse dataset as {fmt}: {exc}") from exc

        # 2. Structural constraints
        if df.empty or len(df) == 0:
            raise DatasetValidationError("Dataset contains 0 rows")

        if len(df.columns) > self.settings.security.max_dataset_columns:
            raise DatasetValidationError(
                f"Dataset column count ({len(df.columns)}) exceeds maximum allowed ({self.settings.security.max_dataset_columns})"
            )

        if len(df) > self.settings.security.max_dataset_rows:
            raise DatasetValidationError(
                f"Dataset row count ({len(df)}) exceeds maximum allowed ({self.settings.security.max_dataset_rows})"
            )

        return df

    def extract_schema(self, df: pd.DataFrame, canonical_bytes: bytes) -> DatasetSchema:
        """Profile dataset columns and construct validated DatasetSchema."""
        columns: list[ColumnSchema] = []
        for col_name in df.columns:
            series = df[col_name]
            ft = self._infer_feature_type(series)
            null_cnt = int(series.isna().sum())
            unique_cnt = int(series.nunique(dropna=True))

            # Grab up to 3 non-null sample values
            samples = [str(x) for x in series.dropna().iloc[:3].tolist()]

            columns.append(
                ColumnSchema(
                    name=str(col_name),
                    feature_type=ft,
                    nullable=null_cnt > 0,
                    null_count=null_cnt,
                    unique_count=unique_cnt,
                    sample_values=samples,
                )
            )

        content_hash = S3StorageService.compute_sha256(canonical_bytes)

        return DatasetSchema(
            columns=columns,
            row_count=len(df),
            column_count=len(df.columns),
            size_bytes=len(canonical_bytes),
            content_hash=f"sha256:{content_hash}",
        )

    def canonicalize_to_parquet(self, df: pd.DataFrame) -> bytes:
        """Convert DataFrame to standardized, compressed Apache Parquet format."""
        out_buf = io.BytesIO()
        df.to_parquet(out_buf, index=False, engine="pyarrow", compression="snappy")
        return out_buf.getvalue()

    def process(self, raw_bytes: bytes, data_format: DatasetFormat | str) -> tuple[pd.DataFrame, bytes, DatasetSchema]:
        """Validate, convert to canonical parquet, and extract complete schema."""
        df = self.parse_dataframe(raw_bytes, data_format)
        parquet_bytes = self.canonicalize_to_parquet(df)
        schema = self.extract_schema(df, parquet_bytes)
        return df, parquet_bytes, schema
