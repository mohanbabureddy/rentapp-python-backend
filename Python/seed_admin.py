"""One-off local script to bootstrap the first ADMIN user directly in Aiven MySQL,
since Render's free tier has no Shell access to run this on the server itself.

Standalone on purpose: importing the app package would pull in Flask, email,
Anthropic, Razorpay, and require JWT_SECRET etc. just to insert one row.

Usage:
  1. Download the CA certificate from Aiven's console and save it as aiven-ca.pem
     in this same folder (already gitignored).
  2. Set DB_URL below (or as an env var) to your Aiven connection string.
  3. Run: python seed_admin.py
"""
import getpass
import os

from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.types import TypeDecorator
from werkzeug.security import generate_password_hash

DB_URL = os.getenv("DB_URL") or "mysql+pymysql://avnadmin:<password>@mysql-350297f3-mohangkbabu-4f4a.i.aivencloud.com:28915/defaultdb"
CA_PATH = os.path.join(os.path.dirname(__file__), "aiven-ca.pem")

Base = declarative_base()


class BitBoolean(TypeDecorator):
    """Matches app/models.py's BitBoolean -- the users table's registration_completed
    column is MySQL BIT(1), left over from the original Java/Hibernate backend."""
    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return 1 if value else 0


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True)
    mail = Column(String(255), nullable=True)
    role = Column(String(50), nullable=True)
    registration_completed = Column(BitBoolean, nullable=False)


engine = create_engine(DB_URL, connect_args={"ssl": {"ca": CA_PATH}})
Session = sessionmaker(bind=engine)
db = Session()

username = os.getenv("ADMIN_USERNAME") or input("Admin username: ").strip()
password = os.getenv("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")

existing = db.query(User).filter_by(username=username).first()
if existing:
    existing.password = generate_password_hash(password)
    existing.registration_completed = True
    db.commit()
    print(f"User '{username}' already existed -- password reset.")
else:
    db.add(User(username=username, password=generate_password_hash(password), role="ADMIN", registration_completed=True))
    db.commit()
    print(f"Admin user '{username}' created.")

db.close()
