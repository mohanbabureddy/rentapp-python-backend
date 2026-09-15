import logging
import os
import random
import re
import smtplib
import socket
import time
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

import bcrypt
from werkzeug.security import check_password_hash, generate_password_hash

from app.models import Complaint, Occupant, TenantBill, TransactionLog, User
from app.repositories import ComplaintRepository, OccupantRepository, TenantBillRepository, TransactionLogRepository, UserRepository

UPLOADS_ROOT = Path(__file__).resolve().parents[1] / "uploads"


def _create_ipv4_connection(address, timeout, source_address=None):
    """Like socket.create_connection, but restricted to IPv4. Render's containers
    have no IPv6 route, yet smtp.gmail.com also publishes an AAAA record -- the
    stdlib tries every resolved address including IPv6, which fails immediately
    with OSError [Errno 101] Network unreachable instead of falling back to the
    IPv4 address that actually works."""
    host, port = address
    err = None
    for family, socktype, proto, _, sockaddr in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is not None:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()
    raise err if err is not None else OSError("getaddrinfo returned no IPv4 addresses")


class _IPv4SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        return _create_ipv4_connection((host, port), timeout, self.source_address)


class _IPv4SMTP_SSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        sock = _create_ipv4_connection((host, port), timeout, self.source_address)
        return self.context.wrap_socket(sock, server_hostname=self._host)


def to_iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime for the API with an explicit UTC marker. Every timestamp in
    this app is set via datetime.utcnow(), which is naive (no tzinfo) -- plain
    dt.isoformat() then produces a string with no timezone indicator at all. The
    browser's `new Date(str)` parses such a string as local time instead of UTC, so
    timestamps render shifted by the viewer's UTC offset (e.g. ~5:30h off in IST).
    Appending "Z" tells the browser it's UTC so it converts to local time correctly."""
    if dt is None:
        return None
    return dt.isoformat() + "Z"


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify a password against either a werkzeug hash or a legacy bcrypt hash
    (accounts created by the old Java/Spring backend, which uses BCryptPasswordEncoder)."""
    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    return check_password_hash(stored_hash, password)


class OTPService:
    OTP_TTL_MILLIS = 5 * 60 * 1000
    otp_storage: Dict[str, Dict[str, Any]] = {}

    def __init__(self, email_service: "EmailService"):
        self.email_service = email_service
        self.logger = logging.getLogger("app.services")

    def generate_otp(self, email: str) -> str:
        otp = str(random.randint(1000, 9999))
        self.otp_storage[email] = {
            "otp": otp,
            "expires_at": time.time() * 1000 + self.OTP_TTL_MILLIS,
        }
        self.logger.info("Generated OTP for %s (valid for 5 minutes).", email)
        self.email_service.send_otp_email(email, otp)
        return otp

    def verify_otp(self, email: str, otp: str) -> bool:
        entry = self.otp_storage.get(email)
        if entry is None:
            self.logger.warning("OTP verification failed for %s: no OTP on record (not requested or already used).", email)
            return False
        if time.time() * 1000 > entry["expires_at"]:
            self.otp_storage.pop(email, None)
            self.logger.warning("OTP verification failed for %s: OTP expired.", email)
            return False
        is_valid = otp == entry["otp"]
        if is_valid:
            self.otp_storage.pop(email, None)
            self.logger.info("OTP verified successfully for %s.", email)
        else:
            self.logger.warning("OTP verification failed for %s: incorrect code.", email)
        return is_valid


class UserService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo
        self.logger = logging.getLogger("app.services")

    def register_user(self, mail: str, password: str) -> User:
        user = User(
            mail=mail,
            password=generate_password_hash(password),
            role="TENANT",
            registration_completed=False,
        )
        saved = self.user_repo.save(user)
        self.logger.info("Registered new tenant account: id=%s mail=%s", saved.id, saved.mail)
        return saved


class TenantBillService:
    def __init__(self, repo: TenantBillRepository, user_repo: UserRepository, email_service: Optional["EmailService"] = None):
        self.repo = repo
        self.user_repo = user_repo
        self.email_service = email_service
        self.logger = logging.getLogger("app.services")

    def get_tenant_bills(self, name: str) -> List[TenantBill]:
        return self.repo.find_by_tenant_name_order_by_month_desc(name)

    def _tenant_user(self, tenant_name: Optional[str]) -> Optional[User]:
        if not tenant_name:
            return None
        return self.user_repo.find_by_username(tenant_name)

    def _admin_user(self) -> Optional[User]:
        for user in self.user_repo.find_all():
            if user.role == "ADMIN":
                return user
        return None

    def mark_paid(self, bill_id: int) -> str:
        bill = self.repo.find_by_id(bill_id)
        if bill is None:
            self.logger.warning("Mark-paid failed: bill %s not found.", bill_id)
            raise ValueError("Bill not found")
        bill.paid = True
        bill.paid_date = datetime.utcnow()
        self.repo.save(bill)
        self.logger.info("Marked bill %s as paid for tenant %s (%s).", bill_id, bill.tenant_name, bill.month_year)
        if self.email_service is not None:
            tenant = self._tenant_user(bill.tenant_name)
            admin = self._admin_user()
            try:
                if tenant and tenant.mail:
                    self.email_service.send_bill_paid_email(bill, tenant.mail, admin.mail if admin and admin.mail else tenant.mail)
                elif admin and admin.mail:
                    self.email_service.send_bill_paid_email(bill, admin.mail, admin.mail)
                else:
                    self.logger.warning(
                        "Skipping payment notification for bill %s: no tenant or admin email on file (tenant=%s, admin=%s).",
                        bill_id, bill.tenant_name, admin.username if admin else None,
                    )
            except Exception:
                self.logger.exception("Failed to send payment notification for bill %s; payment was still recorded.", bill_id)
        return "Payment marked as paid and invoice sent."

    def add_bill(self, bill: TenantBill) -> str:
        existing = self.repo.find_by_tenant_name_and_month(bill.tenant_name, bill.month_year)
        if existing is not None:
            self.logger.warning("Add-bill rejected: bill already exists for tenant %s, month %s.", bill.tenant_name, bill.month_year)
            raise ValueError("Bill already exists for this tenant and month.")
        bill.created_date = date.today()
        self.repo.save(bill)
        self.logger.info("Added new bill for tenant %s, month %s (rent=%s, water=%s, electricity=%s).",
                          bill.tenant_name, bill.month_year, bill.rent, bill.water, bill.electricity)
        if self.email_service is not None:
            tenant = self._tenant_user(bill.tenant_name)
            try:
                if tenant and tenant.mail:
                    self.email_service.notify_bill_generated(bill, tenant.mail, bill.month_year or "current month")
                else:
                    self.logger.warning(
                        "Skipping bill-generated notification for tenant %s: no tenant found or no email on file.",
                        bill.tenant_name,
                    )
            except Exception:
                self.logger.exception("Failed to send bill-generated notification for tenant %s; bill was still saved.", bill.tenant_name)
        return "Bill added successfully and notification triggered."

    def get_all_bills(self) -> List[TenantBill]:
        return self.repo.find_all()

    def delete_bill(self, bill_id: int) -> str:
        self.repo.delete_by_id(bill_id)
        self.logger.info("Deleted bill %s.", bill_id)
        return "Bill deleted successfully."

    def update_bill(self, bill_id: int, updated: TenantBill) -> str:
        bill = self.repo.find_by_id(bill_id)
        if bill is None:
            self.logger.warning("Update-bill failed: bill %s not found.", bill_id)
            raise ValueError("Bill not found")
        bill.tenant_name = updated.tenant_name
        bill.month_year = updated.month_year
        bill.rent = updated.rent
        bill.water = updated.water
        bill.electricity = updated.electricity
        self.repo.save(bill)
        self.logger.info("Updated bill %s for tenant %s (%s).", bill_id, bill.tenant_name, bill.month_year)
        return "Bill updated successfully."

    def get_paid_bills_for_month(self, month_year: str) -> List[TenantBill]:
        return self.repo.find_by_paid_true_and_month(month_year)


class ComplaintService:
    def __init__(self, repo: ComplaintRepository):
        self.repo = repo
        self.logger = logging.getLogger("app.services")

    def create_complaint(self, complaint: Complaint) -> Complaint:
        complaint.status = "OPEN"
        complaint.created_date = datetime.utcnow()
        complaint.closed_date = None
        complaint.resolution_comment = None
        saved = self.repo.save(complaint)
        self.logger.info("Created new complaint %s for tenant %s.", saved.id, saved.tenant_name)
        return saved

    def get_complaints_for_tenant(self, tenant_name: str) -> List[Complaint]:
        return self.repo.find_by_tenant_name_order_by_created_desc(tenant_name)

    def get_all_complaints(self) -> List[Complaint]:
        return self.repo.find_all_order_by_created_desc()

    def close_complaint(self, complaint_id: int, resolution_comment: Optional[str]) -> Complaint:
        complaint = self.repo.find_by_id(complaint_id)
        if complaint is None:
            self.logger.warning("Close-complaint failed: complaint %s not found.", complaint_id)
            raise ValueError("Complaint not found")
        if complaint.status == "CLOSED":
            self.logger.info("Complaint %s is already closed; no action taken.", complaint_id)
            return complaint
        complaint.status = "CLOSED"
        complaint.closed_date = datetime.utcnow()
        if resolution_comment is not None:
            complaint.resolution_comment = resolution_comment.strip()
        saved = self.repo.save(complaint)
        self.logger.info("Closed complaint %s for tenant %s.", complaint_id, saved.tenant_name)
        return saved


class OccupantService:
    def __init__(self, repo: OccupantRepository, user_repo: UserRepository):
        self.repo = repo
        self.user_repo = user_repo
        self.logger = logging.getLogger("app.services")

    def to_dto(self, occupant: Occupant) -> Dict[str, Any]:
        url = None
        if occupant.aadhar_storage_path:
            url = "/uploads/" + occupant.aadhar_storage_path.replace("\\", "/")
        return {
            "id": occupant.id,
            "tenantUsername": occupant.tenant_username,
            "name": occupant.name,
            "aadharFileName": occupant.aadhar_file_name,
            "aadharUrl": url,
            "uploadedAt": to_iso_utc(occupant.uploaded_at),
            "verified": occupant.verified,
        }

    def list(self, tenant_username: str) -> List[Dict[str, Any]]:
        return [self.to_dto(o) for o in self.repo.find_by_tenant_username_order_by_uploaded_desc(tenant_username)]

    def list_all(self) -> List[Dict[str, Any]]:
        return [self.to_dto(o) for o in self.repo.find_all_order_by_uploaded_desc()]

    def add(self, tenant_username: str, name: str, file: Any) -> Dict[str, Any]:
        if not name or not name.strip():
            raise ValueError("Name required")
        if file is None or file.filename == "":
            raise ValueError("File required")
        content_type = file.mimetype
        allowed = ["application/pdf", "image/jpeg", "image/png"]
        if content_type not in allowed:
            self.logger.warning("Occupant upload rejected for %s: invalid file type %s.", tenant_username, content_type)
            raise ValueError("Invalid file type")
        if file.content_length and file.content_length > 2 * 1024 * 1024:
            self.logger.warning("Occupant upload rejected for %s: file too large (%s bytes).", tenant_username, file.content_length)
            raise ValueError("File too large")

        user = self.user_repo.find_by_username(tenant_username)
        if user is None:
            self.logger.warning("Occupant upload rejected: tenant %s not found.", tenant_username)
            raise ValueError("Tenant not found")

        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip())
        ext_map = {
            "image/jpeg": ".jpeg",
            "image/png": ".png",
            "application/pdf": ".pdf",
        }
        file_name = sanitized + ext_map.get(content_type, "")
        target_dir = UPLOADS_ROOT / "aadhaar" / tenant_username
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / file_name
        file.save(target_path)

        occupant = Occupant(
            tenant_username=tenant_username,
            name=name.strip(),
            aadhar_file_name=file.filename,
            aadhar_content_type=content_type,
            aadhar_storage_path=str(Path("aadhaar") / tenant_username / file_name),
        )
        self.repo.save(occupant)
        self.logger.info("Added occupant '%s' for tenant %s (file=%s).", occupant.name, tenant_username, occupant.aadhar_file_name)
        return self.to_dto(occupant)

    def delete(self, occupant_id: int, ignore_verified: bool = False) -> None:
        occupant = self.repo.find_by_id(occupant_id)
        if occupant is None:
            self.logger.warning("Delete-occupant failed: occupant %s not found.", occupant_id)
            raise ValueError("Not found")
        if occupant.verified and not ignore_verified:
            self.logger.warning("Delete-occupant rejected: occupant %s ('%s') is verified.", occupant_id, occupant.name)
            raise ValueError("Cannot delete a verified occupant")
        if occupant.aadhar_storage_path:
            p = Path("uploads") / occupant.aadhar_storage_path
            if p.exists():
                p.unlink(missing_ok=True)
        self.repo.delete(occupant)
        self.logger.info("Deleted occupant %s ('%s') for tenant %s.", occupant_id, occupant.name, occupant.tenant_username)

    def verify_occupant(self, occupant_id: int) -> Dict[str, Any]:
        occupant = self.repo.find_by_id(occupant_id)
        if occupant is None:
            self.logger.warning("Verify-occupant failed: occupant %s not found.", occupant_id)
            raise ValueError("Occupant not found")
        if not occupant.verified:
            occupant.verified = True
            occupant.verified_by = "OWNER"
            occupant.verified_at = datetime.utcnow()
            self.repo.save(occupant)
            self.logger.info("Verified occupant %s ('%s') for tenant %s.", occupant_id, occupant.name, occupant.tenant_username)
        else:
            self.logger.info("Occupant %s ('%s') is already verified; no action taken.", occupant_id, occupant.name)
        return {"status": "ok", "id": occupant.id, "verified": True}

    def find_by_id(self, occupant_id: int) -> Optional[Occupant]:
        return self.repo.find_by_id(occupant_id)


class TransactionService:
    def __init__(self, repo: TransactionLogRepository):
        self.repo = repo
        self.logger = logging.getLogger("app.services")

    def log_success(self, tenant_name: str, payment_id: str) -> TransactionLog:
        log = TransactionLog(tenant_name=tenant_name, payment_id=payment_id, status="SUCCESS")
        saved = self.repo.save(log)
        self.logger.info("Recorded successful payment for tenant %s (payment_id=%s).", tenant_name, payment_id)
        return saved

    def log_failure(self, error_data: Dict[str, Any]) -> TransactionLog:
        payment_id = error_data.get("metadata.payment_id")
        log = TransactionLog(status="FAIL", payment_id=str(payment_id), error_reason=str(error_data))
        saved = self.repo.save(log)
        self.logger.warning("Recorded failed payment attempt (payment_id=%s): %s", payment_id, error_data)
        return saved


class EmailService:
    def __init__(self) -> None:
        self.logger = logging.getLogger("app.email")
        self.logger.setLevel(logging.DEBUG)
        if not self.logger.handlers:
            self.logger.propagate = True

    def _load_dotenv(self) -> Dict[str, str]:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        values: Dict[str, str] = {}
        if not env_path.exists():
            return values

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"\'')
        return values

    def _smtp_settings(self) -> Dict[str, Any]:
        env_values = self._load_dotenv()
        return {
            "server": os.getenv("MAIL_SERVER", env_values.get("MAIL_SERVER", "localhost")),
            "port": int(os.getenv("MAIL_PORT", env_values.get("MAIL_PORT", "25"))),
            "username": os.getenv("MAIL_USERNAME", env_values.get("MAIL_USERNAME")),
            "password": os.getenv("MAIL_PASSWORD", env_values.get("MAIL_PASSWORD")),
            "use_tls": os.getenv("MAIL_USE_TLS", env_values.get("MAIL_USE_TLS", "false")).lower() in {"1", "true", "yes", "on"},
            "use_ssl": os.getenv("MAIL_USE_SSL", env_values.get("MAIL_USE_SSL", "false")).lower() in {"1", "true", "yes", "on"},
            "sender": os.getenv("MAIL_SENDER", env_values.get("MAIL_SENDER")) or os.getenv("MAIL_FROM") or "noreply@localhost",
        }

    def _send_email(self, recipient: str, subject: str, body: str, html_body: Optional[str] = None) -> None:
        if not recipient:
            self.logger.warning("Skipping email send: no recipient address provided. subject=%s", subject)
            return
        settings = self._smtp_settings()
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings["sender"]
        message["To"] = recipient
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

        self.logger.info(
            "Sending email to %s. subject=%s server=%s port=%s username=%s use_tls=%s use_ssl=%s sender=%s",
            recipient, subject, settings["server"], settings["port"], settings["username"],
            settings["use_tls"], settings["use_ssl"], settings["sender"],
        )
        try:
            if settings["use_ssl"]:
                with _IPv4SMTP_SSL(settings["server"], settings["port"], timeout=10) as smtp:
                    if settings["username"]:
                        smtp.login(settings["username"], settings["password"])
                    smtp.send_message(message)
            else:
                with _IPv4SMTP(settings["server"], settings["port"], timeout=10) as smtp:
                    if settings["use_tls"]:
                        smtp.starttls()
                    if settings["username"]:
                        smtp.login(settings["username"], settings["password"])
                    smtp.send_message(message)
        except Exception as exc:  # pragma: no cover - network-dependent path
            self.logger.exception("Mail not sent to %s. SMTP config: server=%s port=%s username=%s use_tls=%s use_ssl=%s sender=%s",
                                  recipient,
                                  settings["server"],
                                  settings["port"],
                                  settings["username"],
                                  settings["use_tls"],
                                  settings["use_ssl"],
                                  settings["sender"])
            raise
        else:
            self.logger.info("Email sent successfully to %s. subject=%s", recipient, subject)

    def _invoice_total(self, bill: TenantBill) -> float:
        rent = bill.rent or 0
        water = bill.water or 0
        electricity = bill.electricity or 0
        return rent + water + electricity

    def _brand_logo(self) -> str:
        return """
        <svg width="420" height="210" viewBox="0 0 420 210" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="VGR logo">
          <g fill="none" fill-rule="evenodd">
            <g transform="translate(42,16)">
              <circle cx="160" cy="90" r="92" stroke="#3e2a1d" stroke-width="12" fill="none"/>
              <g stroke="#3e2a1d" stroke-linecap="round" stroke-width="12">
                <path d="M25 90H15M295 90H305M160 2V-8M160 178V188M66 35L58 27M262 153L270 161M66 145L58 153M262 27L270 19"/>
              </g>
              <path d="M147 60c28 2 45 18 45 42 0 31-27 54-58 54-26 0-45-15-54-40 10 15 27 25 46 25 20 0 38-11 48-29 8-15 10-30 7-52h-34z" fill="#3e2a1d"/>
              <path d="M147 80c30 1 47 15 47 38 0 25-21 44-48 44-18 0-32-8-42-22 9 10 20 16 34 16 23 0 41-15 44-38 2-15-2-30-17-38h-18z" fill="#3e2a1d" opacity="0.9"/>
              <path d="M128 71c26 5 44 22 49 49-9 5-18 9-29 10-23 2-42-7-55-28 4-16 16-31 35-31z" fill="#3e2a1d"/>
            </g>
          </g>
        </svg>
        """

    def _bill_template(self, bill: TenantBill, tenant_name: str, title: str, message: str, footer: str) -> str:
        total = self._invoice_total(bill)
        return f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #1f2937; line-height: 1.6; background-color: #efe8dd; padding: 24px; margin: 0;">
            <div style="max-width: 760px; margin: 0 auto; background: #efe8dd; border: 3px solid #3e2a1d; padding: 28px 24px 24px;">
              <div style="text-align: center; margin-bottom: 12px;">{self._brand_logo()}</div>
              <div style="font-size: 120px; font-weight: 900; letter-spacing: -8px; line-height: 0.9; text-align: center; color: #3e2a1d; margin: 0 0 28px;">VGR</div>

              <h2 style="margin: 0 0 12px; color: #111827; font-size: 24px;">{title}</h2>
              <p style="margin: 0 0 20px; font-size: 15px;">Hello {tenant_name},</p>
              <p style="margin: 0 0 20px; font-size: 15px;">{message}</p>

              <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; background: #ffffff; border: 1px solid #e5e7eb;">
                <tr>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;"><strong>Tenant</strong></td>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb; text-align: right;">{bill.tenant_name}</td>
                </tr>
                <tr>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;"><strong>Billing Month</strong></td>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb; text-align: right;">{bill.month_year}</td>
                </tr>
                <tr>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;"><strong>Rent</strong></td>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb; text-align: right;">₹{bill.rent or 0:.2f}</td>
                </tr>
                <tr>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;"><strong>Water</strong></td>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb; text-align: right;">₹{bill.water or 0:.2f}</td>
                </tr>
                <tr>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;"><strong>Electricity</strong></td>
                  <td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb; text-align: right;">₹{bill.electricity or 0:.2f}</td>
                </tr>
                <tr>
                  <td style="padding: 12px 14px; font-size: 18px;"><strong>Total Due</strong></td>
                  <td style="padding: 12px 14px; font-size: 18px; text-align: right;"><strong>₹{total:.2f}</strong></td>
                </tr>
              </table>

              <p style="margin: 0; color: #374151; font-size: 15px;">{footer}</p>
            </div>
          </body>
        </html>
        """

    def send_otp_email(self, recipient: str, otp: str) -> None:
        subject = "Your OTP code"
        body = f"Your OTP is {otp}. It will expire in 5 minutes.\n\nIf you did not request this, you can ignore this email."
        self._send_email(recipient, subject, body)

    def notify_bill_generated(self, bill: TenantBill, tenant_email: Optional[str], month: str) -> None:
        if tenant_email:
            subject = f"Rent bill generated for {month}"
            body = (
                f"Hello,\n\nYour rent bill for {month} has been generated.\n"
                f"Amount due: ₹{self._invoice_total(bill):.2f}\n\nThank you."
            )
            html_body = self._bill_template(
                bill=bill,
                tenant_name=bill.tenant_name or "Tenant",
                title="Invoice Generated",
                message=f"Your rent invoice for {month} has been generated and is ready for payment.",
                footer="Please pay the total amount before the due date. Thank you.",
            )
            self._send_email(tenant_email, subject, body, html_body)

    def send_bill_paid_email(self, bill: TenantBill, tenant_email: str, admin_email: str) -> None:
        subject = f"Payment received for {bill.month_year}"
        body = (
            f"Hello,\n\nYour payment for {bill.month_year} has been received successfully.\n"
            f"Paid amount: ₹{self._invoice_total(bill):.2f}\n\nThank you."
        )
        html_body = self._bill_template(
            bill=bill,
            tenant_name=bill.tenant_name or "Tenant",
            title="Payment Received",
            message=f"Your payment for {bill.month_year} has been received successfully.",
            footer="Thank you for your payment. This invoice is now marked as paid.",
        )
        self._send_email(tenant_email, subject, body, html_body)
        if admin_email and admin_email != tenant_email:
            self._send_email(admin_email, subject, body, html_body)
