"""Unactivated A24 metadata; no import/startup DDL or migration registration.

The reviewed successor migration must seed a verified origin explicitly.
Neither missing state nor a process restart creates a new stream epoch.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)

metadata = MetaData()
streams = Table(
    "iot_stream_frontier",
    metadata,
    Column("device_id", Uuid(as_uuid=True), primary_key=True),
    Column("stream_epoch", Uuid(as_uuid=True), nullable=False),
    Column("warehouse_id", Uuid(as_uuid=True), nullable=False),
    Column("origin_verified", Boolean, nullable=False, default=False),
    Column("admitted_seq", BigInteger, nullable=False, default=0),
    Column("settled_through", BigInteger, nullable=False, default=0),
    Column("evidence_revision", BigInteger, nullable=False, default=0),
    Column("gap_reason", String(64), nullable=False, default=""),
    UniqueConstraint("device_id", "stream_epoch"),
    CheckConstraint(
        "settled_through >= 0 AND admitted_seq >= settled_through "
        "AND admitted_seq <= 10000 AND evidence_revision >= 0",
        name="ck_iot_stream_frontier_bounds",
    ),
)
entries = Table(
    "iot_stream_admission",
    metadata,
    Column("reception_id", Uuid(as_uuid=True), primary_key=True),
    Column("device_id", Uuid(as_uuid=True), nullable=False),
    Column("stream_epoch", Uuid(as_uuid=True), nullable=False),
    Column("admission_seq", BigInteger, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("observation_id", Uuid(as_uuid=True)),
    UniqueConstraint("device_id", "stream_epoch", "admission_seq"),
    ForeignKeyConstraint(
        ["device_id", "stream_epoch"],
        ["iot_stream_frontier.device_id", "iot_stream_frontier.stream_epoch"],
    ),
    CheckConstraint(
        "admission_seq BETWEEN 1 AND 10000",
        name="ck_iot_stream_admission_sequence",
    ),
    CheckConstraint(
        "outcome IN ('pending','accepted_new','accepted_duplicate','invalid','conflict')",
        name="ck_iot_stream_admission_outcome",
    ),
    CheckConstraint(
        "outcome NOT IN ('accepted_new','accepted_duplicate') OR observation_id IS NOT NULL",
        name="ck_iot_stream_admission_observation",
    ),
)
