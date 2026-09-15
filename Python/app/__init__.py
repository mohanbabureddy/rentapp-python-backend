import logging
import os
import sys
from logging.handlers import RotatingFileHandler

if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from flask import Flask, request
from flask_cors import CORS

from app.database import Base, close_db, engine
from app.routes import register_routes


app = Flask(__name__)
app.teardown_appcontext(close_db)

logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(logs_dir, exist_ok=True)

app.logger.handlers.clear()
file_handler = RotatingFileHandler(
    os.path.join(logs_dir, "app.log"),
    maxBytes=1 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.DEBUG)
app.logger.propagate = False

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
if not any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == file_handler.baseFilename for h in root_logger.handlers):
    root_logger.addHandler(file_handler)

CORS(
    app,
    resources={r"/api/*": {"origins": [
        "http://localhost:3000",
        "https://vgrpay.uk",
        "https://d8aff7a8.rentapp1.pages.dev",
        "https://rentappfrontend-2.onrender.com",
    ]}},
)


@app.before_request
def log_requests():
    payload = None
    if request.is_json:
        payload = request.get_json(silent=True)
    app.logger.info(
        "Incoming request: method=%s path=%s remote_addr=%s query=%s payload=%s",
        request.method,
        request.path,
        request.remote_addr,
        request.query_string.decode("utf-8", errors="replace"),
        payload,
    )


@app.after_request
def log_responses(response):
    app.logger.info(
        "Response: method=%s path=%s status=%s size=%s",
        request.method,
        request.path,
        response.status_code,
        response.content_length,
    )
    return response


Base.metadata.create_all(bind=engine)
register_routes(app)
app.logger.info("Application startup complete")

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
