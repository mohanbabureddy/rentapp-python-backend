from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.types import TypeDecorator

from app.database import Base


class BitBoolean(TypeDecorator):
    """This schema's boolean-like columns (registration_completed, paid, verified) are
    MySQL BIT(1), left over from the original Java/Hibernate backend. PyMySQL returns
    BIT(1) values as raw bytes (b'\\x00'/b'\\x01'), and Python's bool(b'\\x00') is True
    since it checks length, not content -- so plain sqlalchemy.Boolean silently reads
    every false value as true. impl=Integer (not Boolean) is deliberate: Integer's own
    result processor is a no-op, so the raw bytes reach process_result_value below
    unmangled; Boolean's processor would otherwise run first and already turn b'\\x00'
    into True before we ever see it."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return 1 if value else 0

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return value != b"\x00"
        return bool(value)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True)
    mail = Column(String(255), nullable=True)
    role = Column(String(50), nullable=True)
    registration_completed = Column(BitBoolean, default=False, nullable=False)


class TenantBill(Base):
    __tablename__ = "tenant_bills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_name = Column(String(255), nullable=True)
    month_year = Column(String(20), nullable=True)
    rent = Column(Float, nullable=True)
    water = Column(Float, nullable=True)
    electricity = Column(Float, nullable=True)
    miscellaneous = Column(Float, nullable=True)
    paid = Column(BitBoolean, default=False, nullable=False)
    paid_date = Column(DateTime, nullable=True)
    created_date = Column(Date, nullable=True)


class Complaint(Base):
    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=True)
    created_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolution_comment = Column(Text, nullable=True)
    closed_date = Column(DateTime, nullable=True)


class Occupant(Base):
    __tablename__ = "occupants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_username = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    aadhar_file_name = Column(String(255), nullable=True)
    aadhar_content_type = Column(String(255), nullable=True)
    aadhar_storage_path = Column(String(500), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    verified = Column(BitBoolean, default=False, nullable=False)
    verified_by = Column(String(255), nullable=True)
    verified_at = Column(DateTime, nullable=True)


class TransactionLog(Base):
    __tablename__ = "transaction_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_name = Column(String(255), nullable=True)
    payment_id = Column(String(255), nullable=True)
    status = Column(String(50), nullable=True)
    error_reason = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
